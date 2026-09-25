"""Mode Push scoring, v3: the shortcomings found by running it on real signals.

Each test pins one of them. Run against a month of production signals with
sample candidates, the scorer:

* never assessed signal quality — the query feeding it dropped the category;
* ranked recruitment agencies (competitors) as companies to approach;
* ranked companies outside Easy Skill's sectors, and tender buyers;
* listed one employer twice ("Downer" and "Downer Group");
* read news headlines as job adverts, for role, seniority and hiring volume;
* matched "Maintenance Planner" to "Maintenance Coordinator" on the shared word;
* let companies advertising nothing for the candidate reach the mid-fifties and
  seven of the top eight places;
* measured momentum and recency from the clock rather than the data.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest

from push.matcher import (
    NO_DEMAND_CAP,
    company_key,
    exclusions,
    market_as_of,
    match_profile,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)

PLANNER = {"currentTitle": "Maintenance Planner", "sector": "mining", "region": "AU",
           "yearsExperience": 10, "skills": []}


def sig(company="BHP", title="Maintenance Planner", *, kind="job_board", sector="mining",
        category="hiring_velocity", days=1, region="AU", tier=None, rest="Newman WA"):
    return {
        "company_name": company, "raw_content": f"{title} | {company} | {rest}",
        "source_type": kind, "sector": sector, "signal_category": category,
        "geography": "AU", "region": region, "watchlist_tier": tier,
        "captured_at": (NOW - timedelta(days=days)).isoformat(),
    }


def by_name(results):
    return {m.company: m for m in results}


# ---------- who is ranked ----------


def test_recruitment_agencies_are_never_ranked():
    results = match_profile(PLANNER, [sig("Allstar Recruitment Group"), sig("BHP")], now=NOW)
    assert [m.company for m in results] == ["BHP"]


def test_tender_buyers_are_not_employers():
    signals = [sig("Department of Defence", "Maintenance Planner services", kind="tender",
                   sector="defence", category="project"), sig("BHP")]
    assert [m.company for m in match_profile(PLANNER, signals, now=NOW)] == ["BHP"]


def test_companies_outside_the_sectors_are_left_out():
    signals = [sig("Glory Estate Limited", sector="other"), sig("BHP")]
    assert [m.company for m in match_profile(PLANNER, signals, now=NOW)] == ["BHP"]


def test_exclusions_are_counted_by_reason():
    signals = [sig("Allstar Recruitment Group"), sig("Glory Estate", sector="other"),
               sig("Dept", kind="tender"), sig("Unknown"), sig("BHP")]
    assert exclusions(signals) == {"agency": 1, "sector": 1, "tender": 1, "unnamed": 1}


# ---------- one employer, one row ----------


@pytest.mark.parametrize("a,b", [("Downer", "Downer Group"), ("BHP", "BHP Group Limited"),
                                 ("K92 Mining", "K92 Mining Inc."), ("Ventia", "Ventia Pty Ltd")])
def test_legal_suffixes_do_not_split_an_employer(a, b):
    assert company_key(a) == company_key(b)


def test_group_is_only_stripped_from_the_end():
    assert company_key("Group Five") == "group five"


def test_name_variants_are_scored_as_one_company():
    signals = [sig("Downer"), sig("Downer"), sig("Downer Group")]
    results = match_profile(PLANNER, signals, now=NOW)
    assert len(results) == 1
    assert results[0].company == "Downer", "shown as it is most often written"
    assert results[0].signal_count == 3


# ---------- news is not a vacancy ----------


def test_a_headline_is_not_read_as_an_advert():
    news = sig("Newmont", "Newmont appoints new chief executive planner", kind="news",
               category="leadership")
    m = match_profile({**PLANNER, "yearsExperience": 3}, [news], now=NOW)[0]
    role = next(c for c in m.contributions if c.key == "role")
    seniority = next(c for c in m.contributions if c.key == "seniority")
    volume = next(c for c in m.contributions if c.key == "volume")
    assert role.earned == 0 and "No job adverts" in role.evidence
    assert seniority.earned is None, "a 'chief' in a headline is not an advertised level"
    assert volume.earned == 0 and volume.evidence == "No job adverts in the window"


def test_news_still_counts_towards_signal_quality():
    signals = [sig("Newmont", "Maintenance Planner"),
               sig("Newmont", "Newmont approves Lihir expansion", kind="news", category="project")]
    m = match_profile(PLANNER, signals, now=NOW)[0]
    quality = next(c for c in m.contributions if c.key == "signalQuality")
    assert quality.earned is not None and quality.earned > 0
    volume = next(c for c in m.contributions if c.key == "volume")
    assert volume.evidence == "1 job advert in the window"


# ---------- the candidate's trade ----------


def test_sharing_the_domain_word_is_not_the_same_trade():
    m = match_profile(PLANNER, [sig("BHP", "Maintenance Coordinator - Mobile Plant")], now=NOW)[0]
    assert next(c for c in m.contributions if c.key == "role").earned == 0


def test_plurals_and_extra_words_still_match():
    m = match_profile(PLANNER, [sig("BHP", "Senior Maintenance Planners - Shutdowns")], now=NOW)[0]
    assert next(c for c in m.contributions if c.key == "role").earned > 20


# ---------- no demand, no top of the list ----------


def test_a_company_not_hiring_the_discipline_is_held_at_the_cap():
    signals = [sig("Rio Tinto", "Haul Truck Operator", tier="A", category="project")
               for _ in range(8)]
    m = match_profile(PLANNER, signals, now=NOW)[0]
    assert m.demand is False
    assert m.score <= NO_DEMAND_CAP
    assert m.evidence[0].startswith(f"Held at {NO_DEMAND_CAP}")


def test_companies_hiring_the_discipline_rank_first_whatever_the_scores():
    strong_account = [sig("Rio Tinto", "Haul Truck Operator", tier="A", category="project")
                      for _ in range(8)]
    thin_fit = [sig("Small Miner", "Maintenance Planner", days=25)]
    ranked = [m.company for m in match_profile(PLANNER, strong_account + thin_fit, now=NOW)]
    assert ranked == ["Small Miner", "Rio Tinto"]


def test_a_skills_match_lifts_the_cap():
    """Titles vary; an advert asking for the candidate's ticket is demand too."""
    signals = [sig("Monadelphous", "Shutdown Supervisor", rest="SAP PM and shutdown planning")]
    profile = {**PLANNER, "skills": ["sap", "shutdown planning"]}
    m = match_profile(profile, signals, now=NOW)[0]
    assert m.demand is True


# ---------- measured from the data, not the clock ----------


def test_momentum_and_recency_are_measured_from_the_latest_collection():
    """Read weeks later, the same data scores the same."""
    signals = ([sig("BHP", days=1) for _ in range(6)] + [sig("BHP", days=15) for _ in range(3)])
    anchored = match_profile(PLANNER, signals, now=market_as_of(signals))[0]
    unanchored = match_profile(PLANNER, signals)[0]
    assert unanchored.score == anchored.score
    momentum = next(c for c in unanchored.contributions if c.key == "momentum")
    assert momentum.earned and momentum.earned > 0


# ---------- region ----------


def test_region_fit_reads_the_effective_region():
    """A PNG role on an Australian board is PNG work — the digest's rule."""
    png = {**PLANNER, "region": "PNG"}
    m = match_profile(png, [sig("Newmont", region="PNG")], now=NOW)[0]
    assert next(c for c in m.contributions if c.key == "region").earned > 0
    assert m.region == "PNG"


# ---------- one company, same score ----------


def test_scoring_one_company_gives_the_full_ranking_score():
    signals = [sig("BHP"), sig("BHP Group", "Planner"), sig("Downer", "Electrician")]
    full = by_name(match_profile(PLANNER, signals, now=NOW))
    only = match_profile(PLANNER, signals, now=NOW, only_company="bhp group")
    assert len(only) == 1 and only[0].score == full["BHP"].score


# ---------- the query feeding it ----------


def test_signals_for_matching_carries_category_source_and_region(tmp_path, monkeypatch):
    from config.settings import settings as real_settings
    from loader.db import connect
    from loader.ingest import init_db
    from push.store import signals_for_matching

    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    db = tmp_path / "m.db"
    init_db(db, watchlist_path=wl)
    monkeypatch.setattr("loader.db.settings",
                        dataclasses.replace(real_settings, db_path=db, database_url=None))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, captured_at, "
            "geography, region, sector, company_name, signal_category, review_cycle, raw_content, "
            "classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("s1", "news", "newsfeed", "https://x/1", now, "AU", "PNG", "mining", "Newmont",
             "project", "weekly", "Newmont approves expansion | Mining.com.au", now))
    row = signals_for_matching(days=30, target=db)[0]
    assert row["signal_category"] == "project"
    assert row["source_type"] == "news"
    assert row["region"] == "PNG"
