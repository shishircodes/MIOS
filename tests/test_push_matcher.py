"""Tests for push.matcher — the Profile Matcher scoring.

Pure and deterministic: no database, no LLM, so every assertion is exact.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from push.matcher import W_ROLE, MatchResult, match_profile

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)

PLANNER = {"currentTitle": "Maintenance Planner", "sector": "mining", "region": "AU"}


def sig(company, *, title="Maintenance Planner", sector="mining", geo="AU",
        tier=None, days_ago=1):
    captured = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {
        "company_name": company,
        "sector": sector,
        "geography": geo,
        "watchlist_tier": tier,
        "raw_content": f"{title} | {company} | Newman WA | full time",
        "captured_at": captured,
    }


# ---------- ranking ----------


def test_ranks_the_strongest_company_first():
    signals = [
        *[sig("BHP", tier="A") for _ in range(5)],
        sig("Tiny Contractor", title="Office Administrator", sector="other"),
    ]
    results = match_profile(PLANNER, signals, now=NOW)
    assert results[0].company == "BHP"
    assert results[0].score > results[-1].score


def test_score_is_capped_at_100():
    signals = [sig("BHP", tier="A") for _ in range(50)]
    assert match_profile(PLANNER, signals, now=NOW)[0].score <= 100


def test_limit_is_respected():
    signals = [sig(f"Company {i}") for i in range(20)]
    assert len(match_profile(PLANNER, signals, limit=5, now=NOW)) == 5


def test_ties_break_deterministically_by_company_name():
    signals = [sig("Zeta Mining"), sig("Alpha Mining")]
    a = [m.company for m in match_profile(PLANNER, signals, now=NOW)]
    b = [m.company for m in match_profile(PLANNER, signals, now=NOW)]
    assert a == b == sorted(a)


# ---------- contributors ----------


def test_matching_role_scores_higher_than_an_unrelated_one():
    same = match_profile(PLANNER, [sig("A", title="Maintenance Planner")], now=NOW)[0]
    other = match_profile(PLANNER, [sig("B", title="Pastry Chef")], now=NOW)[0]
    assert same.breakdown["role"] > other.breakdown["role"]
    assert other.breakdown["role"] == 0


def test_role_contribution_never_exceeds_its_weight():
    m = match_profile(PLANNER, [sig("A", title="Maintenance Planner")], now=NOW)[0]
    assert 0 < m.breakdown["role"] <= W_ROLE


def test_sector_mismatch_scores_zero_for_sector():
    m = match_profile(PLANNER, [sig("A", sector="defence")], now=NOW)[0]
    assert m.breakdown["sector"] == 0


def test_more_hiring_scores_higher_volume():
    one = match_profile(PLANNER, [sig("A")], now=NOW)[0]
    many = match_profile(PLANNER, [sig("A") for _ in range(8)], now=NOW)[0]
    assert many.breakdown["volume"] > one.breakdown["volume"]


def test_watchlist_client_outranks_an_identical_new_name():
    client = match_profile(PLANNER, [sig("Client Co", tier="A")], now=NOW)[0]
    newname = match_profile(PLANNER, [sig("New Co")], now=NOW)[0]
    assert client.breakdown["relationship"] > newname.breakdown["relationship"]
    # A new name still scores something — it is an opportunity, not a penalty.
    assert newname.breakdown["relationship"] > 0


def test_region_mismatch_is_flagged_as_relocation():
    m = match_profile(PLANNER, [sig("PNG Co", geo="PNG")], now=NOW)[0]
    assert m.breakdown["region"] == 0
    assert any("relocate" in e for e in m.evidence)


def test_stale_signals_lose_their_recency_points():
    fresh = match_profile(PLANNER, [sig("A", days_ago=0)], now=NOW)[0]
    stale = match_profile(PLANNER, [sig("A", days_ago=90)], now=NOW)[0]
    assert fresh.breakdown["recency"] > stale.breakdown["recency"] == 0


# ---------- evidence ----------


def test_every_match_explains_itself():
    """A score with no reason is not something a consultant can act on."""
    m = match_profile(PLANNER, [sig("BHP", tier="A") for _ in range(3)], now=NOW)[0]
    assert m.evidence, "a match must carry evidence"
    assert all(isinstance(e, str) and e for e in m.evidence)


def test_breakdown_sums_to_the_score():
    m = match_profile(PLANNER, [sig("BHP", tier="A") for _ in range(3)], now=NOW)[0]
    # The breakdown sums to what was earned; the score is that scaled to 100
    # over the contributors that could be assessed.
    assert sum(m.breakdown.values()) == m.earned
    assert m.score == round(m.earned / m.assessable * 100)


def test_evidence_leads_with_role_demand():
    m = match_profile(PLANNER, [sig("BHP", tier="A") for _ in range(3)], now=NOW)[0]
    assert "matching" in m.evidence[0]


# ---------- presentation ----------


def test_watchlist_client_gets_an_mpc_email_action():
    m = match_profile(PLANNER, [sig("BHP", tier="A")], now=NOW)[0]
    assert m.recommended_action == "Send MPC email"
    assert m.relationship.startswith("TIER A")


def test_new_name_gets_cold_outreach():
    m = match_profile(PLANNER, [sig("Unknown Co")], now=NOW)[0]
    assert m.recommended_action == "Cold outreach"
    assert m.relationship == "NEW NAME"


def test_to_dict_matches_the_shape_the_ui_renders():
    m = match_profile(PLANNER, [sig("BHP", tier="A")], now=NOW)[0]
    d = m.to_dict(rank=1)
    for key in ("rank", "co", "score", "rel", "region", "sector", "evidence", "action"):
        assert key in d, f"the Push page reads {key}"


# ---------- edge cases ----------


def test_unidentified_employers_are_excluded():
    """The classifier writes 'Unknown' when it cannot name the employer — those
    cannot be approached, so they are not match candidates."""
    results = match_profile(PLANNER, [sig("Unknown"), sig("")], now=NOW)
    assert results == []


def test_no_signals_yields_no_matches():
    assert match_profile(PLANNER, [], now=NOW) == []


def test_profile_without_a_title_still_matches_on_other_factors():
    m = match_profile({"sector": "mining", "region": "AU"}, [sig("BHP", tier="A")], now=NOW)[0]
    # Not merely zero: with no title on the profile there is nothing to judge,
    # so the contributor leaves the denominator rather than scoring nothing.
    assert "role" in m.not_assessed
    assert "role" not in m.breakdown
    assert m.score > 0


def test_empty_profile_does_not_crash():
    results = match_profile({}, [sig("BHP", tier="A")], now=NOW)
    assert isinstance(results[0], MatchResult)


def test_malformed_capture_dates_are_ignored():
    bad = sig("A")
    bad["captured_at"] = "not-a-date"
    m = match_profile(PLANNER, [bad], now=NOW)[0]
    assert "recency" in m.not_assessed
    assert m.score > 0
