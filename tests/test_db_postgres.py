"""Parity tests: the pipeline must behave identically on PostgreSQL and SQLite.

These need a real server and are skipped unless one is configured:

    TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_db_postgres.py

Point it at a scratch database — `_clean` truncates the tables between tests.
Never aim it at a database holding real signals. It deliberately does NOT fall
back to `DATABASE_URL`, so a stray `pytest` can't wipe your Neon data.

A local server is enough to catch dialect bugs:
    docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=mios postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:pw@localhost:55432/mios?sslmode=disable
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from delivery.digest import build_digest
from loader.db import SCHEMA_PATH, connect
from loader.ingest import ingest, init_db, wipe_signals

PG_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="set TEST_DATABASE_URL to run PostgreSQL parity tests"
)


@pytest.fixture
def watchlist(tmp_path):
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "x",
         "aliases": ["BHP Group"]},
        {"company_name": "Newmont", "tier": "A", "sector": "mining", "notes": "y",
         "aliases": []},
    ]))
    return p


@pytest.fixture
def pg(watchlist):
    """A schema-loaded, empty PostgreSQL database."""
    with connect(PG_URL) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    with connect(PG_URL) as conn:
        conn.execute("DELETE FROM signals")
        conn.execute("DELETE FROM watchlist")
        conn.execute("DROP TABLE IF EXISTS kv_store")
    init_db(PG_URL, watchlist_path=watchlist)
    yield PG_URL
    with connect(PG_URL) as conn:
        conn.execute("DELETE FROM signals")
        conn.execute("DELETE FROM watchlist")


def _count(target, table) -> int:
    with connect(target) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# ---------- schema + seeding ----------


def test_schema_applies_to_postgres(pg):
    """The single schema.sql must be valid PostgreSQL, not just SQLite."""
    assert _count(pg, "signals") == 0
    assert _count(pg, "watchlist") == 2


def test_init_db_is_idempotent_on_postgres(pg, watchlist):
    init_db(pg, watchlist_path=watchlist)
    init_db(pg, watchlist_path=watchlist)
    assert _count(pg, "watchlist") == 2  # ON CONFLICT DO UPDATE, not duplicated


# ---------- ingestion ----------


def test_ingest_and_dedupe_on_postgres(pg):
    n = ingest([
        {"source_url": "https://x/1", "raw_content": "Planner at BHP Pilbara"},
        {"source_url": "https://x/2", "raw_content": "Engineer at Newmont Lihir"},
    ], pg)
    assert n == 2
    assert ingest([{"source_url": "https://x/1", "raw_content": "again"}], pg) == 0
    assert _count(pg, "signals") == 2


def test_duplicate_does_not_abort_the_surrounding_batch(pg):
    """Regression: Postgres aborts a transaction on constraint violation.

    Catching the error and rolling back would have discarded every row inserted
    earlier in the same batch, so ingest uses ON CONFLICT DO NOTHING instead.
    A duplicate in the middle must not cost us the rows around it.
    """
    ingest([{"source_url": "https://x/dup", "raw_content": "first"}], pg)
    written = ingest([
        {"source_url": "https://x/a", "raw_content": "before the duplicate"},
        {"source_url": "https://x/dup", "raw_content": "the duplicate"},
        {"source_url": "https://x/b", "raw_content": "after the duplicate"},
    ], pg)
    assert written == 2
    assert _count(pg, "signals") == 3


def test_null_source_urls_do_not_collide(pg):
    """The unique index is partial (WHERE source_url IS NOT NULL), so many rows
    may have no URL. Both engines must honour that."""
    n = ingest([
        {"source_url": None, "raw_content": "first with no url"},
        {"source_url": None, "raw_content": "second with no url"},
    ], pg)
    assert n == 2


def test_wipe_signals_keeps_watchlist(pg):
    ingest([{"source_url": "https://x/1", "raw_content": "abc"}], pg)
    wipe_signals(pg)
    assert _count(pg, "signals") == 0
    assert _count(pg, "watchlist") == 2


# ---------- reads ----------


def test_digest_reads_from_postgres(pg):
    now = datetime.now(timezone.utc)
    with connect(pg) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, sector, company_name, watchlist_tier, "
            "signal_category, review_cycle, raw_content, analysis_notes, "
            "is_new_prospect, classified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("s1", "job_board", "seek", "https://x/9", now.isoformat(timespec="seconds"),
             "AU", "mining", "BHP", "A", "leadership", "weekly",
             "New GM appointed at BHP Pilbara", "leadership change", 0,
             now.isoformat(timespec="seconds")),
        )
    text = build_digest(pg, since=now - timedelta(days=7))
    assert "BHP" in text
    assert "Key Signals" in text


def test_kv_store_counter_increments_on_postgres(pg):
    """The quota counter casts an integer back to TEXT — Postgres rejects the
    unqualified arithmetic that SQLite's loose typing accepts."""
    from agents.signal_analyst import (
        _ensure_kv_store,
        _get_daily_api_calls,
        _increment_daily_api_calls,
    )

    with connect(pg) as conn:
        _ensure_kv_store(conn)
        assert _get_daily_api_calls(conn) == 0
        for _ in range(3):
            _increment_daily_api_calls(conn)
        assert _get_daily_api_calls(conn) == 3


def test_like_pattern_survives_placeholder_translation(pg):
    """`LIKE 'gemini_api_calls_%'` must not be mangled into a bad placeholder."""
    from agents.signal_analyst import _ensure_kv_store, _increment_daily_api_calls

    with connect(pg) as conn:
        _ensure_kv_store(conn)
        _increment_daily_api_calls(conn)
    with connect(pg) as conn:
        conn.execute("DELETE FROM kv_store WHERE key LIKE 'gemini_api_calls_%'")
    with connect(pg) as conn:
        assert conn.execute("SELECT COUNT(*) FROM kv_store").fetchone()[0] == 0


# ---------- migration ----------


def test_migrate_copies_sqlite_into_postgres(pg, tmp_path, watchlist):
    from loader.migrate import migrate

    sqlite_db = tmp_path / "source.db"
    init_db(sqlite_db, watchlist_path=watchlist)
    ingest([
        {"source_url": "https://x/m1", "raw_content": "row one"},
        {"source_url": "https://x/m2", "raw_content": "row two"},
    ], sqlite_db)

    with connect(pg) as conn:
        conn.execute("DELETE FROM signals")

    s = migrate(source=sqlite_db, target=pg)
    assert s["signals_written"] == 2
    assert s["watchlist_written"] >= 0
    assert _count(pg, "signals") == 2

    # Re-running must not duplicate.
    again = migrate(source=sqlite_db, target=pg)
    assert again["signals_written"] == 0
    assert _count(pg, "signals") == 2
