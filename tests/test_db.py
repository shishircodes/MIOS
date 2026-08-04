"""Tests for the dual-backend database layer (loader.db).

The pure parts — placeholder translation, DSN normalisation, row access — are
tested unconditionally. Anything needing a live PostgreSQL is in
`test_db_postgres.py` and skips when no server is configured.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from config.settings import settings as real_settings
from loader.db import (
    Row,
    _to_pg,
    connect,
    describe,
    is_postgres,
    normalise_pg_dsn,
    resolve_target,
)

NEON = "postgresql://user:pw@ep-cool-name-123.ap-southeast-2.aws.neon.tech/mios"


# ---------- placeholder translation ----------


def test_placeholders_become_pg_style():
    assert _to_pg("SELECT * FROM t WHERE a = ? AND b = ?") == (
        "SELECT * FROM t WHERE a = %s AND b = %s"
    )


def test_question_marks_inside_string_literals_are_left_alone():
    sql = "SELECT * FROM t WHERE note = 'why? because' AND id = ?"
    assert _to_pg(sql) == "SELECT * FROM t WHERE note = 'why? because' AND id = %s"


def test_percent_is_escaped_so_psycopg_does_not_read_it_as_a_placeholder():
    # Regression: `LIKE 'gemini_api_calls_%'` in the KPI harness. Unescaped, the
    # driver reads %' as a placeholder and the statement blows up.
    sql = "DELETE FROM kv_store WHERE key LIKE 'gemini_api_calls_%' AND id = ?"
    out = _to_pg(sql)
    assert "'gemini_api_calls_%%'" in out
    assert out.endswith("id = %s")


def test_doubled_quotes_inside_a_literal_do_not_end_it_early():
    sql = "SELECT * FROM t WHERE s = 'it''s ? here' AND id = ?"
    assert _to_pg(sql) == "SELECT * FROM t WHERE s = 'it''s ? here' AND id = %s"


# ---------- DSN handling ----------


@pytest.mark.parametrize("dsn,expected", [
    (NEON, True),
    ("postgres://u:p@host/db", True),
    ("postgresql+psycopg://u:p@host/db", True),
    ("data/mios.db", False),
])
def test_is_postgres_detects_dsn_forms(dsn, expected):
    assert is_postgres(dsn) is expected


def test_path_is_never_treated_as_postgres():
    assert is_postgres(Path("data/mios.db")) is False


def test_legacy_postgres_scheme_is_rewritten_for_psycopg():
    # psycopg rejects the `postgres://` form that many tools still emit.
    assert normalise_pg_dsn("postgres://u:p@h/db").startswith("postgresql://")


def test_sqlalchemy_style_scheme_is_rewritten():
    assert normalise_pg_dsn("postgresql+psycopg://u:p@h/db").startswith("postgresql://")


def test_sslmode_is_added_because_neon_refuses_plaintext():
    assert "sslmode=require" in normalise_pg_dsn(NEON)


def test_existing_sslmode_is_respected():
    dsn = normalise_pg_dsn(NEON + "?sslmode=verify-full")
    assert "sslmode=verify-full" in dsn
    assert dsn.count("sslmode=") == 1


def test_sslmode_appends_correctly_when_other_params_present():
    dsn = normalise_pg_dsn(NEON + "?application_name=mios")
    assert "application_name=mios&sslmode=require" in dsn


# ---------- credential safety ----------


def test_describe_never_leaks_the_password():
    out = describe(NEON)
    assert "pw" not in out
    assert "ep-cool-name-123" in out and "/mios" in out


def test_describe_labels_sqlite_paths():
    assert describe(Path("data/mios.db")).startswith("sqlite:")


# ---------- target resolution ----------


def test_database_url_wins_over_db_path(monkeypatch):
    patched = dataclasses.replace(real_settings, database_url=NEON)
    monkeypatch.setattr("loader.db.settings", patched)
    assert resolve_target() == NEON


def test_falls_back_to_sqlite_when_no_database_url(monkeypatch):
    patched = dataclasses.replace(real_settings, database_url="")
    monkeypatch.setattr("loader.db.settings", patched)
    assert resolve_target() == patched.db_path


def test_explicit_argument_beats_configuration(monkeypatch):
    patched = dataclasses.replace(real_settings, database_url=NEON)
    monkeypatch.setattr("loader.db.settings", patched)
    assert resolve_target(Path("/tmp/other.db")) == Path("/tmp/other.db")


# ---------- Row ----------


def test_row_supports_key_index_and_unpacking():
    """sqlite3.Row allows all three; psycopg dict rows allow only the first.
    Callers here rely on all three, so Row has to cover them."""
    r = Row(["a", "b"], [1, 2])
    assert r["a"] == 1
    assert r[0] == 1
    assert r[1] == 2
    a, b = r
    assert (a, b) == (1, 2)
    assert dict(r) == {"a": 1, "b": 2}
    assert list(r.keys()) == ["a", "b"]
    assert len(r) == 2


# ---------- SQLite path through the shared layer ----------


def test_connect_creates_parent_directories(tmp_path):
    target = tmp_path / "nested" / "deeper" / "mios.db"
    with connect(target) as conn:
        conn.execute("CREATE TABLE t (id TEXT)")
    assert target.exists()


def test_connect_commits_on_clean_exit(tmp_path):
    target = tmp_path / "m.db"
    with connect(target) as conn:
        conn.execute("CREATE TABLE t (id TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", ("x",))
    with connect(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_connect_rolls_back_on_error(tmp_path):
    target = tmp_path / "m.db"
    with connect(target) as conn:
        conn.execute("CREATE TABLE t (id TEXT)")
    with pytest.raises(RuntimeError):
        with connect(target) as conn:
            conn.execute("INSERT INTO t VALUES (?)", ("y",))
            raise RuntimeError("boom")
    with connect(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
