"""Tests for the written half of a Mode Push result.

One property matters more than any other here and most of these defend it: the
model may write about the ranking, and may disagree with it out loud, but it
must never change it. The score decides who a consultant contacts, so it stays
reproducible; a model that quietly reorders the list would make every number
above it unfalsifiable.

The rest is about failing softly. The deterministic ranking is the product and
the rationale is an improvement on it, so every way this can go wrong — no key,
spent quota, a malformed reply, an invented company — has to return the ranking
unannotated rather than fail the request.

No live model is involved. A test that depends on a daily quota is not a test.
"""
from __future__ import annotations

import pytest

from llm import LLMError, QuotaExhausted
from push.rationale import (
    ANNOTATE_TOP_N,
    FIT_STRONG,
    FIT_WEAK,
    annotate,
    build_prompt,
)

PROFILE = {
    "currentTitle": "Maintenance Planner",
    "sector": "mining",
    "region": "AU",
    "yearsExperience": 9,
    "skills": ["SAP", "shutdown planning"],
}


def match(co: str, rank: int, score: int, **extra):
    row = {
        "rank": rank, "co": co, "score": score, "rel": "Tier A client",
        "signalCount": 5, "confidence": "medium",
        "evidence": [f"{co} is hiring 5 maintenance planners"],
        "breakdown": {"role": 28, "skills": 7},
    }
    row.update(extra)
    return row


RANKED = [match("BHP", 1, 87), match("Rio Tinto", 2, 71), match("Newcrest", 3, 60)]


def reply(entries):
    def _call(_system, _user, _schema=None):
        return entries
    return _call


# ---------- the order is not the model's to change ----------


def test_the_ranking_order_is_untouched():
    """The property this whole design rests on."""
    out, _ = annotate(PROFILE, [dict(m) for m in RANKED], caller=reply([
        {"company": "Newcrest", "rationale": "Best fit by far.", "fit": FIT_STRONG},
        {"company": "BHP", "rationale": "Weak.", "fit": FIT_WEAK},
    ]))

    assert [m["co"] for m in out] == ["BHP", "Rio Tinto", "Newcrest"]
    assert [m["score"] for m in out] == [87, 71, 60]


def test_a_disagreement_is_flagged_rather_than_acted_on():
    """A model that can only agree is decoration. One that silently reorders is
    worse. So it says so, and a human looks."""
    out, _ = annotate(PROFILE, [dict(m) for m in RANKED], caller=reply([
        {"company": "BHP", "rationale": "All of this is one stale project.", "fit": FIT_WEAK},
    ]))

    bhp = next(m for m in out if m["co"] == "BHP")
    assert bhp["disagrees"] is True
    assert bhp["score"] == 87, "the score must not move"
    assert out[0]["co"] == "BHP", "nor the position"


def test_agreement_is_not_flagged():
    out, _ = annotate(PROFILE, [dict(m) for m in RANKED], caller=reply([
        {"company": "BHP", "rationale": "Hiring planners at Newman.", "fit": FIT_STRONG},
    ]))
    assert next(m for m in out if m["co"] == "BHP")["disagrees"] is False


# ---------- what comes back is not trusted ----------


def test_a_company_that_was_not_ranked_is_discarded():
    """A model naming a company the pipeline never saw would put it in front of
    a consultant with nothing behind it."""
    out, _ = annotate(PROFILE, [dict(m) for m in RANKED], caller=reply([
        {"company": "Glencore", "rationale": "Invented.", "fit": FIT_STRONG},
        {"company": "BHP", "rationale": "Real.", "fit": FIT_STRONG},
    ]))

    assert [m["co"] for m in out] == ["BHP", "Rio Tinto", "Newcrest"]
    assert next(m for m in out if m["co"] == "BHP")["rationale"] == "Real."
    assert all("Glencore" not in m["co"] for m in out)


def test_an_unlabelled_verdict_is_dropped_not_defaulted():
    """Defaulting to "possible" would put a judgement in the model's mouth."""
    out, _ = annotate(PROFILE, [dict(m) for m in RANKED], caller=reply([
        {"company": "BHP", "rationale": "Hiring planners.", "fit": "quite good actually"},
    ]))
    assert next(m for m in out if m["co"] == "BHP")["fit"] == ""


def test_an_empty_rationale_is_not_attached():
    out, _ = annotate(PROFILE, [dict(m) for m in RANKED], caller=reply([
        {"company": "BHP", "rationale": "   ", "fit": FIT_STRONG},
    ]))
    assert "rationale" not in next(m for m in out if m["co"] == "BHP")


# ---------- every failure returns the ranking intact ----------


@pytest.mark.parametrize("bad", [
    lambda *a, **k: {"not": "a list"},
    lambda *a, **k: None,
    lambda *a, **k: "plain text, not json",
    lambda *a, **k: [],
])
def test_a_malformed_reply_returns_the_ranking_unannotated(bad):
    out, note = annotate(PROFILE, [dict(m) for m in RANKED], caller=bad)

    assert [m["co"] for m in out] == ["BHP", "Rio Tinto", "Newcrest"]
    assert note, "a silent no-op leaves nobody able to tell it did nothing"


def test_a_spent_quota_returns_the_ranking_and_says_so():
    """The case that will actually happen: the free tier allows twenty requests
    a day and the weekly pipeline already spends some."""
    def _boom(*_a, **_k):
        raise QuotaExhausted("429 RESOURCE_EXHAUSTED")

    out, note = annotate(PROFILE, [dict(m) for m in RANKED], caller=_boom)

    assert len(out) == 3
    assert "429" in note


def test_no_model_configured_returns_the_ranking_and_says_so():
    def _unconfigured(*_a, **_k):
        raise LLMError("GEMINI_API_KEY is not set")

    out, note = annotate(PROFILE, [dict(m) for m in RANKED], caller=_unconfigured)

    assert len(out) == 3
    assert "GEMINI_API_KEY" in note


def test_an_unexpected_error_still_returns_the_ranking():
    def _boom(*_a, **_k):
        raise ValueError("something else entirely")

    out, note = annotate(PROFILE, [dict(m) for m in RANKED], caller=_boom)
    assert len(out) == 3
    assert "ValueError" in note


def test_an_empty_ranking_makes_no_call():
    calls = []

    def _call(*a, **k):
        calls.append(1)
        return []

    out, note = annotate(PROFILE, [], caller=_call)
    assert out == []
    assert calls == []


# ---------- one call, not one per company ----------


def test_only_one_call_is_made_for_the_whole_list():
    """A call per company would exhaust a twenty-request day on one candidate."""
    calls = []

    def _call(_s, _u, _schema=None):
        calls.append(1)
        return [{"company": "BHP", "rationale": "Hiring.", "fit": FIT_STRONG}]

    annotate(PROFILE, [dict(m) for m in RANKED], caller=_call)
    assert len(calls) == 1


def test_only_the_top_of_the_list_is_described():
    """Paying for prose about the twentieth company spends quota on something
    nobody reads."""
    long_list = [match(f"Co{i}", i, 90 - i) for i in range(1, 20)]
    seen = {}

    def _call(_s, user, _schema=None):
        seen["prompt"] = user
        return []

    annotate(PROFILE, long_list, caller=_call, top_n=ANNOTATE_TOP_N)
    assert "Co1" in seen["prompt"]
    assert "Co19" not in seen["prompt"]


# ---------- what the model is shown ----------


def test_the_prompt_carries_the_evidence_and_the_breakdown():
    """A model that can see a score was carried by relationship rather than role
    demand can say something useful about it — that is the disagreement worth
    surfacing."""
    prompt = build_prompt(PROFILE, RANKED)

    assert "Maintenance Planner" in prompt
    assert "SAP" in prompt
    assert "is hiring 5 maintenance planners" in prompt
    assert "points:" in prompt


def test_the_prompt_names_the_candidates_own_details():
    prompt = build_prompt(PROFILE, RANKED)
    assert "9 years" in prompt
    assert "mining" in prompt
