"""Tokens and estimated cost for the admin screen.

What is pinned: tokens a provider reports reach the log without any caller
changing; each model is priced at its own rate, including Claude's cache rates;
a model with no published rate is counted but never given a guessed cost; and
a time frame covers exactly the days it says.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import llm.providers as providers
import llm.usage as usage
from llm.pricing import estimate, rate_for
from loader.db import connect
from loader.ingest import init_db

NOW = datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    path = tmp_path / "usage.db"
    init_db(path, watchlist_path=wl)
    return path


def _log(db, *, days_ago=0, provider="anthropic", model="claude-sonnet-5", purpose="pulse",
         ok=True, inp=1_000_000, out=100_000, cache_read=None, cache_write=None):
    when = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO llm_call_log (id, called_at, purpose, provider, model, ok, input_tokens, "
            "output_tokens, cache_read_tokens, cache_write_tokens) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"{when}-{model}-{days_ago}-{inp}", when, purpose, provider, model, 1 if ok else 0,
             inp if ok else None, out if ok else None, cache_read, cache_write),
        )


# ---------- pricing ----------


def test_claude_is_priced_at_its_own_rates():
    # Sonnet 5: $2 in, $10 out per million.
    assert estimate("anthropic", "claude-sonnet-5",
                    input_tokens=1_000_000, output_tokens=100_000) == pytest.approx(3.00)


def test_claude_cache_reads_and_writes_have_their_own_rates():
    # Opus 5: $5 in; a cache read is a tenth of that, a write 1.25x.
    cost = estimate("anthropic", "claude-opus-5",
                    cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    assert cost == pytest.approx(0.50 + 6.25)


def test_gemini_pro_charges_more_for_a_long_prompt():
    short = estimate("gemini", "gemini-2.5-pro", input_tokens=100_000)
    long = estimate("gemini", "gemini-2.5-pro", input_tokens=300_000)
    assert long / 3 > short, "the per-token rate rises past 200k prompt tokens"


def test_a_dated_snapshot_prices_as_its_model():
    assert rate_for("anthropic", "claude-haiku-4-5-20251001") == rate_for("anthropic", "claude-haiku-4-5")


def test_an_unknown_model_gets_no_estimate_rather_than_a_guess():
    assert estimate("gemini", "gemini-2.0-flash", input_tokens=1000, output_tokens=1000) is None


# ---------- recording ----------


def test_a_providers_reported_tokens_reach_the_log(db, monkeypatch):
    """Callers keep the (system, user, schema) signature; the provider's callable
    carries the tokens on itself and the seam passes them on."""
    def build(model):
        def _call(s, u, sc=None):
            _call.last_usage = {"input_tokens": 1200, "output_tokens": 300,
                                "cache_read_tokens": 50, "cache_write_tokens": 0}
            return {"ok": True}
        return _call

    monkeypatch.setattr(providers._PROVIDERS["gemini"], "build", build)
    monkeypatch.setattr(usage, "connect", lambda t=None, **kw: connect(db, **kw))

    providers.caller_for("classify")("system", "user", None)

    with connect(db) as conn:
        row = dict(conn.execute("SELECT * FROM llm_call_log").fetchone())
    assert (row["purpose"], row["provider"], row["ok"]) == ("classify", "gemini", 1)
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]) == (1200, 300, 50)


def test_a_failed_call_is_logged_with_unknown_tokens_not_zero(db, monkeypatch):
    monkeypatch.setattr(usage, "connect", lambda t=None, **kw: connect(db, **kw))
    usage.record("pulse", "anthropic", "claude-opus-5", ok=False, note="APIError")

    with connect(db) as conn:
        row = dict(conn.execute("SELECT * FROM llm_call_log").fetchone())
    assert row["ok"] == 0 and row["input_tokens"] is None


def test_an_old_database_without_the_log_starts_recording(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with connect(path) as conn:
        conn.execute("CREATE TABLE placeholder (id INTEGER)")
    monkeypatch.setattr(usage, "connect", lambda t=None, **kw: connect(path, **kw))

    usage.record("push", "gemini", "gemini-2.5-flash", ok=True, input_tokens=10, output_tokens=5)

    with connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM llm_call_log").fetchone()[0] == 1


# ---------- the report ----------


def test_a_time_frame_covers_exactly_its_days(db):
    _log(db, days_ago=0)
    _log(db, days_ago=6)
    _log(db, days_ago=7)   # outside seven days

    week = usage.report("7d", now=NOW, target=db)
    assert week["totals"]["calls"] == 2
    assert len(week["daily"]) == 7
    assert week["daily"][-1]["date"] == "2026-09-15"

    assert usage.report("today", now=NOW, target=db)["totals"]["calls"] == 1
    assert usage.report("30d", now=NOW, target=db)["totals"]["calls"] == 3


def test_costs_are_split_by_model_and_by_job(db):
    _log(db, model="claude-sonnet-5", purpose="pulse", inp=1_000_000, out=100_000)       # $3.00
    _log(db, provider="gemini", model="gemini-2.5-flash", purpose="classify",
         inp=1_000_000, out=0, days_ago=1)                                                 # $0.30

    r = usage.report("7d", now=NOW, target=db)

    assert r["totals"]["costUsd"] == pytest.approx(3.30)
    top = r["byModel"][0]
    assert (top["model"], top["costUsd"]) == ("claude-sonnet-5", pytest.approx(3.00))
    jobs = {p["purpose"]: p for p in r["byPurpose"]}
    assert jobs["classify"]["costUsd"] == pytest.approx(0.30)
    assert jobs["pulse"]["label"] == "Market Pulse"


def test_an_unpriced_model_is_counted_but_not_costed(db):
    _log(db, provider="gemini", model="gemini-2.0-flash", inp=5000, out=500)

    r = usage.report("today", now=NOW, target=db)
    model = r["byModel"][0]
    assert model["priced"] is False
    assert model["inputTokens"] == 5000
    assert model["costUsd"] == 0 and model["unpricedCalls"] == 1


def test_failed_calls_are_counted_without_tokens(db):
    _log(db, ok=False)
    t = usage.report("today", now=NOW, target=db)["totals"]
    assert (t["calls"], t["failed"], t["inputTokens"], t["costUsd"]) == (1, 1, 0, 0)


def test_no_log_yet_is_an_empty_report_not_an_error(tmp_path):
    path = tmp_path / "empty.db"
    with connect(path) as conn:
        conn.execute("CREATE TABLE placeholder (id INTEGER)")
    r = usage.report("30d", now=NOW, target=path)
    assert r["totals"]["calls"] == 0 and r["trackedSince"] is None
    assert len(r["daily"]) == 30


def test_an_unknown_range_falls_back_to_thirty_days(db):
    assert usage.report("fortnight", now=NOW, target=db)["range"] == "30d"
