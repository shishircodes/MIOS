"""Tests for loader.ingest."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from loader.ingest import init_db, ingest, wipe_signals


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def watchlist(tmp_path: Path) -> Path:
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "x", "aliases": ["BHP Group"]},
        {"company_name": "Newmont", "tier": "A", "sector": "mining", "notes": "y", "aliases": []},
    ]))
    return p


def _count(db: Path, table: str) -> int:
    with sqlite3.connect(db) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_init_db_creates_tables_and_seeds_watchlist(db, watchlist):
    init_db(db, watchlist_path=watchlist)
    assert db.exists()
    assert _count(db, "signals") == 0
    assert _count(db, "watchlist") == 2


def test_init_db_is_idempotent(db, watchlist):
    init_db(db, watchlist_path=watchlist)
    init_db(db, watchlist_path=watchlist)
    assert _count(db, "watchlist") == 2  # INSERT OR REPLACE keeps count stable


def test_ingest_writes_records_and_generates_uuid(db, watchlist):
    init_db(db, watchlist_path=watchlist)
    n = ingest(
        [
            {"source_url": "https://x/1", "raw_content": "Maintenance Planner role at BHP Pilbara"},
            {"source_url": "https://x/2", "raw_content": "Reliability Engineer Newmont Lihir"},
        ],
        db,
    )
    assert n == 2
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT signal_id, source_url FROM signals ORDER BY source_url").fetchall()
    assert {r[1] for r in rows} == {"https://x/1", "https://x/2"}
    assert all(len(r[0]) >= 32 for r in rows)  # UUIDs


def test_ingest_dedupes_by_source_url(db, watchlist):
    init_db(db, watchlist_path=watchlist)
    ingest([{"source_url": "https://x/dup", "raw_content": "first"}], db)
    inserted = ingest([{"source_url": "https://x/dup", "raw_content": "second"}], db)
    assert inserted == 0
    assert _count(db, "signals") == 1


def test_ingest_skips_empty_raw_content(db, watchlist):
    init_db(db, watchlist_path=watchlist)
    n = ingest([{"source_url": "https://x/empty", "raw_content": "   "}], db)
    assert n == 0
    assert _count(db, "signals") == 0


def test_wipe_signals(db, watchlist):
    init_db(db, watchlist_path=watchlist)
    ingest([{"source_url": "https://x/1", "raw_content": "abc"}], db)
    assert _count(db, "signals") == 1
    wipe_signals(db)
    assert _count(db, "signals") == 0
    assert _count(db, "watchlist") == 2  # watchlist preserved


def test_columns_added_after_first_deploy_reach_an_existing_database(tmp_path):
    """Regression: `CREATE TABLE IF NOT EXISTS` does nothing when the table is
    already there, so a column added to schema.sql later never appeared on a
    database created before it. It failed at query time with "column does not
    exist" — on production, not in any test."""
    import json

    from loader.db import connect
    from loader.ingest import ADDED_COLUMNS, _existing_columns, init_db

    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    db = tmp_path / "m.db"
    init_db(db, watchlist_path=wl)

    # Simulate a database created before the column existed.
    table, column, _decl = ADDED_COLUMNS[0]
    with connect(db) as conn:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        assert column not in _existing_columns(conn, table)

    init_db(db, watchlist_path=wl)

    with connect(db) as conn:
        assert column in _existing_columns(conn, table), "the migration did not run"


def test_the_column_migration_is_idempotent(tmp_path):
    import json

    from loader.ingest import init_db

    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    db = tmp_path / "m2.db"
    for _ in range(3):
        init_db(db, watchlist_path=wl)  # must not raise "duplicate column"

