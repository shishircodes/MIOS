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
