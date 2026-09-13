"""The evaluation harness's quota reset clears the counter that is actually kept.

The reset deleted `gemini_api_calls_%` by hand while the counter had moved to
`llm_calls:<provider>:<date>`. Nothing failed: the DELETE matched no rows and
returned quietly, so each evaluation run inherited the previous run's count and
could refuse to classify partway through a five-run harness — with the reason
reading as an exhausted daily quota rather than a stale counter.

So this asserts the two ends meet, by writing through the counter's own
recorder and reading through its own reader. A test that named either key as a
string would have gone stale in exactly the same silence as the code did.
"""
from __future__ import annotations

import pytest

from evaluation.kpi_harness import _reset_daily_quota
from llm import usage
from loader.db import connect


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "quota.db"
    with connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)")
    return path


def test_the_reset_clears_what_the_recorder_wrote(db):
    for _ in range(3):
        usage.record("classify", "gemini", "gemini-2.5-flash", ok=True, target=db)
    assert usage.used_today("gemini", db) == 3

    _reset_daily_quota(db)

    assert usage.used_today("gemini", db) == 0


def test_every_provider_is_reset_not_only_the_default(db):
    """The harness resets a run's quota, not one vendor's."""
    usage.record("classify", "gemini", "gemini-2.5-flash", ok=True, target=db)
    usage.record("classify", "anthropic", "claude-sonnet-5", ok=True, target=db)

    _reset_daily_quota(db)

    assert usage.used_today("gemini", db) == 0
    assert usage.used_today("anthropic", db) == 0


def test_unrelated_rows_survive(db):
    """It resets a counter; the rest of `kv_store` is not its business."""
    with connect(db) as conn:
        conn.execute("INSERT INTO kv_store(key, value) VALUES(?, ?)", ("last_run_id", "7"))
    usage.record("classify", "gemini", "gemini-2.5-flash", ok=True, target=db)

    _reset_daily_quota(db)

    with connect(db, readonly=True) as conn:
        row = conn.execute(
            "SELECT value FROM kv_store WHERE key = ?", ("last_run_id",)).fetchone()
    assert row["value"] == "7"
