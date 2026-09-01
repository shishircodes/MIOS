"""Tests for the v2 Mode Push scoring.

The four contributors added in v2 all read data the pipeline was already
collecting and the old scorer ignored: the candidate's skills, what *kind* of
signal was detected, whether hiring is accelerating, and how senior the roles
are. What is pinned here is mostly that they discriminate — the old model gave
two companies with the same job titles and the same volume nearly the same
score, however differently they were behaving.

Weights are judgement, not calibration: nobody has been placed through this yet.
So these test the shape of the model — that more of a good thing scores higher,
that absent evidence scores nothing rather than something — and deliberately not
the exact numbers, which should be free to move when the BD team has opinions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from push.matcher import (
    CATEGORY_WEIGHT,
    W_MOMENTUM,
    W_RECENCY,
    W_REGION,
    W_RELATIONSHIP,
    W_ROLE,
    W_SECTOR,
    W_SENIORITY,
    W_SIGNAL_QUALITY,
    W_SKILLS,
    W_VOLUME,
    _confidence,
    _momentum,
    _seniority_fit,
    _signal_quality,
    _skills_overlap,
    match_profile,
)

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def sig(days=2, content="Maintenance Planner | BHP | Newman",
        category="hiring_velocity", **extra):
    row = {
        "company_name": "BHP",
        "raw_content": content,
        "signal_category": category,
        "sector": "mining",
        "geography": "AU",
        "captured_at": (NOW - timedelta(days=days)).isoformat(),
    }
    row.update(extra)
    return row


def test_the_weights_still_sum_to_one_hundred():
    """A score out of 100 that cannot reach 100, or can exceed it, makes every
    number on the screen a lie. This caught a rebalance that summed to 110."""
    total = (W_ROLE + W_SKILLS + W_SIGNAL_QUALITY + W_SECTOR + W_MOMENTUM
             + W_VOLUME + W_RELATIONSHIP + W_SENIORITY + W_REGION + W_RECENCY)
    assert total == 100


# ---------- skills ----------


def test_skills_present_in_the_adverts_score(db=None):
    pts, ev = _skills_overlap(["SAP", "shutdown planning"],
                              [sig(content="Planner | BHP | SAP and shutdown planning")])
    assert pts > 0
    assert "SAP" in ev


def test_skills_score_in_proportion_to_how_many_matched():
    two = _skills_overlap(["SAP", "AMOS"], [sig(content="Planner | SAP AMOS")])[0]
    one = _skills_overlap(["SAP", "AMOS"], [sig(content="Planner | SAP only")])[0]
    assert two > one > 0


def test_a_skill_must_match_as_a_whole_word():
    """Fuzzy matching is right for a job title, where word order and
    abbreviation vary. A skill is a noun somebody either asked for or did not."""
    pts, _ = _skills_overlap(["SAP"], [sig(content="Planner | SAPPHIRE mine services")])
    assert pts == 0


def test_no_skills_on_the_profile_scores_nothing_silently():
    assert _skills_overlap([], [sig()]) == (0, None)
    assert _skills_overlap(None, [sig()]) == (0, None)


# ---------- signal quality ----------


def test_a_project_signal_outscores_routine_hiring():
    """The largest thing the old model could not see: six ordinary vacancies and
    six postings around a new mine counted identically."""
    project = _signal_quality([sig(category="project") for _ in range(3)])[0]
    routine = _signal_quality([sig(category="hiring_velocity") for _ in range(3)])[0]
    assert project > routine


def test_signal_quality_is_averaged_not_summed():
    """Volume is already scored separately; rewarding it twice would let a
    company outrank a better-timed one simply by posting more."""
    three = _signal_quality([sig(category="project") for _ in range(3)])[0]
    six = _signal_quality([sig(category="project") for _ in range(6)])[0]
    assert three == six


def test_a_decision_point_says_so_in_its_evidence():
    _, ev = _signal_quality([sig(category="leadership")])
    assert "routine" in ev.lower()


def test_an_unknown_category_does_not_crash_or_dominate():
    pts, _ = _signal_quality([sig(category="something_new")])
    assert 0 < pts < W_SIGNAL_QUALITY


# ---------- momentum ----------


def _rows(recent: int, prior: int):
    return ([{"captured_at": (NOW - timedelta(days=2)).isoformat()} for _ in range(recent)]
            + [{"captured_at": (NOW - timedelta(days=16)).isoformat()} for _ in range(prior)])


def test_accelerating_hiring_scores():
    assert _momentum(_rows(8, 6), NOW)[0] > 0


def test_steady_hiring_scores_nothing():
    """A company that always posts forty roles is not newsworthy at forty. The
    comparison is against itself, not against other companies."""
    assert _momentum(_rows(4, 12), NOW)[0] == 0


def test_a_company_with_no_history_has_no_momentum():
    """Inventing a trend from a single window is how the digest's velocity table
    once reported every company as rising, including one never seen before."""
    assert _momentum(_rows(9, 0), NOW) == (0, None)


def test_a_tiny_baseline_is_described_rather_than_quoted_as_a_percentage():
    """Two signals against a baseline of 0.3 is "up 567%" — true, useless, and
    the first thing a client would question."""
    pts, ev = _momentum(_rows(5, 2), NOW)
    assert pts > 0
    assert "%" not in ev


def test_a_real_baseline_is_quoted(caplog):
    _, ev = _momentum(_rows(8, 12), NOW)
    assert "%" in ev


# ---------- seniority ----------


def test_experience_matching_the_advertised_level_scores():
    pts, _ = _seniority_fit(9, [sig(content="Senior Maintenance Planner | BHP")])
    assert pts > 0


def test_a_graduate_role_does_not_fit_a_twenty_year_career():
    pts, ev = _seniority_fit(20, [sig(content="Graduate Maintenance Planner | BHP")])
    assert pts == 0
    assert "20" in ev


def test_an_unknown_level_scores_nothing_and_claims_nothing():
    """Silence rather than a default: an evidence line asserting a fit that was
    never established is worse than no line."""
    assert _seniority_fit(9, [sig(content="Maintenance Planner | BHP")]) == (0, None)
    assert _seniority_fit(None, [sig(content="Senior Planner | BHP")]) == (0, None)


# ---------- confidence ----------


def test_confidence_separates_a_thin_case_from_a_strong_one():
    """The point of reporting it beside the score: both can reach the same
    number from very different amounts of evidence."""
    thin, _ = _confidence([sig()], NOW)
    broad, _ = _confidence([sig(days=2) for _ in range(6)] + [sig(days=9) for _ in range(4)], NOW)
    assert thin == "low"
    assert broad == "high"


def test_a_low_confidence_result_says_what_to_do_with_it():
    _, note = _confidence([sig()], NOW)
    assert "lead" in note.lower()


# ---------- the model as a whole ----------


def test_the_better_timed_company_outranks_the_busier_one():
    """The case the old model could not distinguish. Both are hiring the same
    role in the same sector at the same volume. One is opening a project, has
    the candidate's skills in its adverts, and is accelerating."""
    profile = {"currentTitle": "Maintenance Planner", "sector": "mining", "region": "AU",
               "yearsExperience": 9, "skills": ["SAP", "shutdown planning"]}

    signals = []
    for _ in range(5):
        signals.append(sig(company_name="Alpha", days=2, category="project",
                           content="Senior Maintenance Planner | Alpha | SAP, shutdown planning"))
    for _ in range(2):
        signals.append(sig(company_name="Alpha", days=16))
    for _ in range(5):
        signals.append(sig(company_name="Beta", days=2,
                           content="Maintenance Planner | Beta | Pilbara"))
    for _ in range(5):
        signals.append(sig(company_name="Beta", days=16,
                           content="Maintenance Planner | Beta | Pilbara"))

    ranked = match_profile(profile, signals, now=NOW)
    assert [m.company for m in ranked][0] == "Alpha"
    assert ranked[0].score > ranked[1].score


def test_every_contributor_is_reported_in_the_breakdown():
    """A score a consultant cannot take apart is one they cannot defend."""
    profile = {"currentTitle": "Maintenance Planner", "sector": "mining",
               "region": "AU", "yearsExperience": 9, "skills": ["SAP"]}
    ranked = match_profile(profile, [sig()], now=NOW)

    assert set(ranked[0].breakdown) == {
        "role", "skills", "signalQuality", "sector", "momentum",
        "volume", "relationship", "seniority", "region", "recency",
    }


def test_the_score_never_exceeds_one_hundred():
    profile = {"currentTitle": "Senior Maintenance Planner", "sector": "mining",
               "region": "AU", "yearsExperience": 8, "skills": ["SAP"]}
    signals = [sig(company_name="Alpha", days=1, category="project", tier="A",
                   watchlist_tier="A",
                   content="Senior Maintenance Planner | Alpha | SAP") for _ in range(40)]
    signals += [sig(company_name="Alpha", days=16, watchlist_tier="A") for _ in range(2)]

    assert match_profile(profile, signals, now=NOW)[0].score <= 100
