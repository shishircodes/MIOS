"""Tests for the Gemini rewrite pass (publish.rewrite).

Gemini is faked throughout — a test that depends on a live model and a daily
quota is not a test. What is being pinned here is the boundary: the model may
change wording, and may not change facts.
"""
from __future__ import annotations

import json

import pytest

from loader.db import connect
from loader.ingest import init_db
from publish.report import Section
from publish.rewrite import (
    DAILY_API_CALL_LIMIT,
    NEVER_REWRITE,
    invented_numbers,
    rewrite,
)


@pytest.fixture
def watchlist(tmp_path):
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    return p


@pytest.fixture
def db(tmp_path, watchlist):
    path = tmp_path / "rewrite.db"
    init_db(path, watchlist_path=watchlist)
    return path


COMPUTED = [
    Section("Executive Summary",
            "MIOS collected and classified 396 hiring signals during 2026-Q3. "
            "Mining accounted for 41% of activity."),
    Section("Australia — Mining",
            "MIOS detected 126 hiring signals across 50 mining employers. "
            "The most active were BHP (68)."),
    Section("Looking Ahead", "", source="manual"),
    Section("Methodology", "No figure in this report is estimated."),
]


def _caller(bodies: dict[str, str]):
    """A fake Gemini that returns exactly what the test wants it to."""
    def _call(_system, _user, _schema=None):
        return {"sections": [{"heading": h, "body": b} for h, b in bodies.items()]}
    return _call


# ---------- the fabrication guard ----------


def test_invented_numbers_spots_a_figure_that_was_not_there():
    assert invented_numbers("126 signals", "126 signals, up 40%") == {"40%"}


def test_dropping_a_figure_is_allowed():
    """Trimming is an editorial choice; adding is fabrication. The check is
    deliberately one-directional."""
    assert invented_numbers("126 signals across 50 employers", "126 signals") == set()


def test_reformatting_the_same_number_is_not_an_invention():
    assert invented_numbers("1,250 postings", "1250 postings") == set()


def test_a_section_that_invents_a_figure_is_discarded(db):
    fake = _caller({
        "Executive Summary": "Hiring surged 40% this quarter across 396 signals.",
        "Australia — Mining": "MIOS detected 126 signals across 50 mining employers, led by BHP (68).",
    })
    out = rewrite(COMPUTED, target=db, gemini_caller=fake)

    summary = next(s for s in out.sections if s.heading == "Executive Summary")
    mining = next(s for s in out.sections if s.heading == "Australia — Mining")

    assert summary.body == COMPUTED[0].body, "the invented 40% must not ship"
    assert "led by BHP (68)" in mining.body, "a clean rewrite is kept"
    assert out.rejected == ["Executive Summary"]
    assert "not in the data" in (out.reason or "")


def test_a_clean_rewrite_is_accepted(db):
    fake = _caller({
        "Executive Summary": "Across 2026-Q3, MIOS classified 396 hiring signals, "
                             "with mining representing 41% of all activity.",
        "Australia — Mining": "Australian mining produced 126 signals from 50 employers, "
                              "with BHP (68) the most active.",
    })
    out = rewrite(COMPUTED, target=db, gemini_caller=fake)
    assert out.used_llm is True
    assert out.rejected is None
    assert "representing 41% of all activity" in out.sections[0].body


# ---------- what is never sent ----------


def test_the_outlook_and_methodology_are_never_rewritten(db):
    """The outlook is empty by design and the methodology carries the
    disclaimers. Neither is worth a paraphrase."""
    sent: list[str] = []

    def _call(_system, user, _schema=None):
        sent.append(user)
        return {"sections": [{"heading": "Methodology", "body": "Everything is estimated."},
                             {"heading": "Looking Ahead", "body": "Boom times ahead."}]}

    out = rewrite(COMPUTED, target=db, gemini_caller=_call)
    prompt = sent[0]
    for heading in NEVER_REWRITE:
        assert heading not in prompt, f"{heading} was sent to the model"

    method = next(s for s in out.sections if s.heading == "Methodology")
    outlook = next(s for s in out.sections if s.heading == "Looking Ahead")
    assert method.body == "No figure in this report is estimated.", "disclaimer intact"
    assert outlook.body == "", "the outlook stays for a human to write"


def test_every_section_comes_back_even_when_the_model_omits_one(db):
    fake = _caller({"Executive Summary": "396 signals in 2026-Q3, mining at 41%."})
    out = rewrite(COMPUTED, target=db, gemini_caller=fake)
    assert [s.heading for s in out.sections] == [s.heading for s in COMPUTED]
    mining = next(s for s in out.sections if s.heading == "Australia — Mining")
    assert mining.body == COMPUTED[1].body, "an omitted section keeps its computed prose"


# ---------- free-tier limits ----------


def test_the_whole_report_costs_one_call(db):
    calls = []

    def _call(_s, _u, _sc=None):
        calls.append(1)
        return {"sections": []}

    out = rewrite(COMPUTED, target=db, gemini_caller=_call)
    assert len(calls) == 1, "one call for the report, not one per section"
    assert out.calls_used == 1


def test_the_call_is_charged_to_the_shared_daily_budget(db):
    """The same counter classification uses. A report must not quietly starve
    the pipeline that produces next week's signals."""
    from agents.signal_analyst import _ensure_kv_store, _get_daily_api_calls

    with connect(db) as conn:
        _ensure_kv_store(conn)
        before = _get_daily_api_calls(conn)

    rewrite(COMPUTED, target=db, gemini_caller=_caller({}))

    with connect(db) as conn:
        assert _get_daily_api_calls(conn) == before + 1


def test_a_failed_call_still_counts_against_the_budget(db):
    """Gemini charges for the attempt, so pretending it did not happen would
    let a loop of failures blow through the quota invisibly."""
    from agents.signal_analyst import _ensure_kv_store, _get_daily_api_calls

    def _boom(_s, _u, _sc=None):
        return {"sections": [{"heading": "Executive Summary", "body": "396 signals, up 40%."}]}

    with connect(db) as conn:
        _ensure_kv_store(conn)
        before = _get_daily_api_calls(conn)

    rewrite(COMPUTED, target=db, gemini_caller=_boom)
    with connect(db) as conn:
        assert _get_daily_api_calls(conn) == before + 1


def test_an_exhausted_quota_falls_back_without_calling(db, monkeypatch):
    monkeypatch.setattr("publish.rewrite._remaining_quota", lambda _t: 0)
    monkeypatch.setattr("publish.rewrite.settings",
                        type("S", (), {"gemini_api_key": "present"})())

    out = rewrite(COMPUTED, target=db)
    assert out.used_llm is False
    assert str(DAILY_API_CALL_LIMIT) in (out.reason or "")
    assert out.sections == COMPUTED, "the computed report still ships"


def test_a_missing_api_key_falls_back_and_says_so(db, monkeypatch):
    monkeypatch.setattr("publish.rewrite.settings",
                        type("S", (), {"gemini_api_key": ""})())
    out = rewrite(COMPUTED, target=db)
    assert out.used_llm is False
    assert "No Gemini key" in (out.reason or "")


# ---------- failure is never fatal ----------


def test_a_gemini_exception_leaves_the_report_intact(db):
    def _raise(_s, _u, _sc=None):
        raise RuntimeError("503 model overloaded")

    out = rewrite(COMPUTED, target=db, gemini_caller=_raise)
    assert out.used_llm is False
    assert out.sections == COMPUTED
    assert "503" in (out.reason or "")


def test_a_malformed_response_is_survivable(db):
    def _junk(_s, _u, _sc=None):
        return {"unexpected": "shape"}

    out = rewrite(COMPUTED, target=db, gemini_caller=_junk)
    assert out.sections == COMPUTED


def test_nothing_to_rewrite_makes_no_call(db):
    calls = []

    def _call(_s, _u, _sc=None):
        calls.append(1)
        return {"sections": []}

    out = rewrite([Section("Looking Ahead", "", source="manual")],
                  target=db, gemini_caller=_call)
    assert calls == []
    assert out.used_llm is False
