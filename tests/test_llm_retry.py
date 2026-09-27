"""Retrying AI calls that failed on the provider's side.

A single Gemini `ServerError` cost a batch of 25 records on 21 Sep and again on
27 Sep 2026: the calls either side succeeded, and nothing retried. Pinned: a
server error is retried and the batch is saved; a rate limit waits for the
window; anything else fails at once; and the waits are the ones stated.
"""
from __future__ import annotations

import json

import pytest

import llm.retry as retry
from llm.retry import is_rate_limit, is_server_error, with_retry


class ServerError(Exception):
    """Named like google-genai's, which is how the real one is recognised."""


class Flaky:
    def __init__(self, failures: list[Exception], result="ok"):
        self.failures = list(failures)
        self.result = result
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.result


@pytest.fixture
def waits(monkeypatch):
    seen: list[float] = []
    monkeypatch.setattr(retry, "_sleep", seen.append)
    return seen


# ---------- telling failures apart ----------


@pytest.mark.parametrize("exc", [
    ServerError("500 INTERNAL"),
    RuntimeError("503 UNAVAILABLE. The model is overloaded. Please try again later."),
    RuntimeError("Deadline exceeded"),
    type("InternalServerError", (Exception,), {})("boom"),
    type("APIConnectionError", (Exception,), {})("connection reset"),
])
def test_provider_side_failures_are_recognised(exc):
    assert is_server_error(exc)


def test_a_status_code_attribute_is_read():
    exc = Exception("something")
    exc.code = 502
    assert is_server_error(exc)


@pytest.mark.parametrize("exc", [
    RuntimeError("429 RESOURCE_EXHAUSTED"),
    RuntimeError("Quota exceeded for requests per minute"),
])
def test_rate_limits_are_not_server_errors(exc):
    assert is_rate_limit(exc) and not is_server_error(exc)


@pytest.mark.parametrize("exc", [ValueError("Expecting ',' delimiter"),
                                 RuntimeError("400 INVALID_ARGUMENT")])
def test_other_failures_are_neither(exc):
    assert not is_server_error(exc) and not is_rate_limit(exc)


# ---------- retrying ----------


def test_a_server_error_is_retried_after_a_short_wait(waits):
    fn = Flaky([ServerError("503")])
    assert with_retry(fn, "a", b=1) == "ok"
    assert fn.calls == 2
    assert waits == [retry.SERVER_RETRY_WAITS[0]]


def test_server_errors_are_retried_twice_then_raised(waits):
    fn = Flaky([ServerError("1"), ServerError("2"), ServerError("3")])
    with pytest.raises(ServerError, match="3"):
        with_retry(fn)
    assert fn.calls == 3
    assert waits == list(retry.SERVER_RETRY_WAITS)


def test_a_bad_request_is_not_retried(waits):
    fn = Flaky([ValueError("bad JSON")])
    with pytest.raises(ValueError):
        with_retry(fn)
    assert fn.calls == 1 and waits == []


def test_a_rate_limit_waits_a_minute_only_when_asked(waits):
    with pytest.raises(RuntimeError):
        with_retry(Flaky([RuntimeError("429 quota")]))
    assert waits == [], "a daily quota will not come back by waiting"

    fn = Flaky([RuntimeError("429 quota")])
    assert with_retry(fn, rate_limit_retries=2) == "ok"
    assert waits == [retry.RATE_LIMIT_WAIT]


# ---------- where it is used ----------


def test_one_server_error_no_longer_costs_a_batch(tmp_path):
    """The 27 Sep run: one ServerError, 25 records left unclassified."""
    from agents.signal_analyst import classify_pending
    from loader.ingest import ingest, init_db

    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    db = tmp_path / "r.db"
    init_db(db, watchlist_path=wl)
    ingest([{"source_url": f"u{i}", "raw_content":
             f"Maintenance Planner {i} at BHP Pilbara, FIFO 8/6 ex-Perth, roles open."}
            for i in range(30)], db)

    calls = {"n": 0}

    def gemini(_sys, prompt, schema=None, **_kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ServerError("500 INTERNAL")
        n = prompt.upper().count("--- SIGNAL ")
        return [{"company_name": "BHP", "sector": "mining", "signal_category": "hiring_velocity",
                 "review_cycle": "weekly", "watchlist_match": None, "is_new_prospect": False,
                 "reasoning": "x"} for _ in range(n)]

    counts = classify_pending(db, gemini_caller=gemini)
    assert counts["classified"] == 30
    assert not counts.get("errors")
    assert calls["n"] == 3, "two batches, one retried"


def test_the_market_pulse_survives_a_hiccup(tmp_path):
    from delivery.pulse import generate_pulse
    from loader.ingest import init_db

    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    db = tmp_path / "p.db"
    init_db(db, watchlist_path=wl)
    payload = {"collection": {"collected": 12}, "signals": [
        {"company": "BHP", "title": "Maintenance Planner", "sector": "Mining", "region": "AU"}],
        "velocity": [], "collectedFrom": "2026-09-20T00:00:00+00:00",
        "collectedTo": "2026-09-27T00:00:00+00:00"}
    bullets = {"bullets": [{"text": f"Point {i} about BHP hiring.", "kind": "fact"}
                           for i in range(3)]}
    fn = Flaky([ServerError("503 overloaded")], result=bullets)
    out = generate_pulse(payload, target=db, gemini_caller=fn)
    assert fn.calls == 2
    assert out.ok, out.note
