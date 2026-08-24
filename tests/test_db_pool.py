"""Tests for the connection pool and the read-only fast path (loader.db).

Opening a Postgres connection costs roughly seven network round trips, which is
most of the cost of a short query once the API and the database are not in the
same region. Pooling removes them — but only if the transaction semantics
survive, which is what these pin.

SQLite is what runs here, so these cover the parts that are engine-independent:
the contract of `connect()`, and that `readonly` does not quietly become a way
to lose a write.
"""
from __future__ import annotations

import json

import pytest

from loader.db import close_pool, connect
from loader.ingest import init_db


@pytest.fixture
def db(tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    path = tmp_path / "pool.db"
    init_db(path, watchlist_path=wl)
    with connect(path) as conn:
        conn.execute("DELETE FROM app_users")
    return path


def _count(db) -> int:
    with connect(db, readonly=True) as conn:
        return int(conn.execute("SELECT count(*) FROM app_users").fetchone()[0])


def _insert(conn, email: str) -> None:
    conn.execute(
        "INSERT INTO app_users (email, role, added_by, added_at) VALUES (?,?,?,?)",
        (email, "member", "test", "2026-01-01T00:00:00+00:00"),
    )


# ---------- the contract has not changed ----------


def test_a_clean_block_commits(db):
    with connect(db) as conn:
        _insert(conn, "a@example.com")
    assert _count(db) == 1


def test_a_failed_block_writes_nothing(db):
    """The whole point of the surrounding transaction: a run that dies halfway
    must not leave half its rows behind."""
    with pytest.raises(RuntimeError):
        with connect(db) as conn:
            _insert(conn, "a@example.com")
            _insert(conn, "b@example.com")
            raise RuntimeError("halfway")

    assert _count(db) == 0, "a partial write survived the rollback"


def test_the_original_error_is_not_swallowed_by_the_rollback(db):
    with pytest.raises(RuntimeError, match="the real problem"):
        with connect(db) as conn:
            _insert(conn, "a@example.com")
            raise RuntimeError("the real problem")


# ---------- the read-only path ----------


def test_a_read_only_block_reads(db):
    with connect(db) as conn:
        _insert(conn, "a@example.com")
    with connect(db, readonly=True) as conn:
        assert conn.execute("SELECT count(*) FROM app_users").fetchone()[0] == 1


def test_read_only_is_opt_in(db):
    """It skips the surrounding transaction, so a caller that writes inside one
    would lose all-or-nothing behaviour. Defaulting to it would make that a
    silent property of every existing caller."""
    import inspect

    sig = inspect.signature(connect)
    assert sig.parameters["readonly"].default is False
    assert sig.parameters["readonly"].kind is inspect.Parameter.KEYWORD_ONLY


def test_connections_are_reusable_in_sequence(db):
    """Whatever the backend does underneath, one block must not leave the next
    one holding something unusable."""
    for i in range(5):
        with connect(db) as conn:
            _insert(conn, f"u{i}@example.com")
    assert _count(db) == 5


def test_closing_the_pool_is_safe_when_there_is_not_one(db):
    close_pool()
    close_pool()
    # And the module still works afterwards.
    assert _count(db) == 0
