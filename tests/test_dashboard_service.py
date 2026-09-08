"""Tests for the trends dashboard.

The page this replaces was built entirely on invented numbers — a hardcoded
twelve-week series, fabricated sector totals, a literal 20 for the watchlist,
and an "↑ trending" delta on every tile with nothing compared. It was the most
confident-looking screen in the product and the only one where nothing on it was
true, and the invented series ran an order of magnitude high: 847 Australian
roles a week against a real 73.

So most of these are about the figures being counted rather than asserted, and
about the two ways this kind of page lies when data is thin — claiming a period
it does not have, and showing a movement it has not measured.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from api.dashboard_service import TREND_COLLECTIONS, build_dashboard_payload
from loader.db import connect
from loader.ingest import init_db


@pytest.fixture
def db(tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
        {"company_name": "Newmont", "tier": "B", "sector": "mining", "notes": "", "aliases": []},
    ]))
    path = tmp_path / "dash.db"
    init_db(path, watchlist_path=wl)
    with connect(path) as conn:
        conn.execute("DELETE FROM signals")
    return path


def add(db, *, day: str, region: str = "AU", sector: str = "mining",
        classified: bool = True, n: int = 1):
    with connect(db) as conn:
        for i in range(n):
            sid = f"{day}-{region}-{sector}-{i}-{classified}"
            conn.execute(
                "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
                "captured_at, geography, region, sector, company_name, watchlist_tier, "
                "signal_category, review_cycle, raw_content, analysis_notes, is_new_prospect, "
                "classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, "job_board", "seek", f"https://x/{sid}", f"{day}T00:41:05+00:00",
                 region, region, sector, "BHP", "A", "hiring_velocity", "weekly",
                 "BHP is hiring", "note", 0,
                 f"{day}T00:41:05+00:00" if classified else None),
            )


# ---------- counted, not asserted ----------


def test_the_series_counts_what_each_collection_found(db):
    add(db, day="2026-08-10", region="AU", n=4)
    add(db, day="2026-08-10", region="PNG", n=2)
    add(db, day="2026-08-17", region="AU", n=6)

    payload = build_dashboard_payload(db)
    assert [(c["date"], c["au"], c["png"]) for c in payload["collections"]] == [
        ("2026-08-10", 4, 2),
        ("2026-08-17", 6, 0),
    ]


def test_the_watchlist_total_is_read_not_hardcoded(db):
    """The old page printed a literal 20."""
    payload = build_dashboard_payload(db)
    assert payload["watchlist"]["total"] == 2
    assert payload["watchlist"]["byTier"] == {"A": 1, "B": 1}


def test_sectors_come_from_the_most_recent_collection(db):
    add(db, day="2026-08-10", sector="construction", n=5)
    add(db, day="2026-08-17", sector="mining", n=3)
    add(db, day="2026-08-17", sector="oil_gas", n=1)

    sectors = build_dashboard_payload(db)["sectors"]
    assert [(s["key"], s["count"]) for s in sectors] == [("mining", 3), ("oil_gas", 1)]
    assert [s["label"] for s in sectors] == ["Mining", "Oil & gas"]


def test_an_unclassified_row_is_not_counted(db):
    """It has no sector or region yet, so counting it would move the totals
    without being able to say where."""
    add(db, day="2026-08-17", n=3)
    add(db, day="2026-08-17", n=5, classified=False)

    assert build_dashboard_payload(db)["latest"]["total"] == 3


# ---------- movement is measured, or absent ----------


def test_the_change_is_against_the_previous_collection(db):
    add(db, day="2026-08-10", region="AU", n=10)
    add(db, day="2026-08-17", region="AU", n=15)

    assert build_dashboard_payload(db)["change"]["au"] == 50.0


def test_a_fall_is_reported_as_a_fall(db):
    """The old page's tiles said "↑ trending" whatever had happened."""
    add(db, day="2026-08-10", region="AU", n=10)
    add(db, day="2026-08-17", region="AU", n=8)

    assert build_dashboard_payload(db)["change"]["au"] == -20.0


def test_a_first_collection_has_no_movement_to_report(db):
    """None, not zero: there was no comparison, and "no change" would claim one
    was made."""
    add(db, day="2026-08-17", region="AU", n=10)

    change = build_dashboard_payload(db)["change"]
    assert change["au"] is None
    assert change["total"] is None


def test_growth_from_nothing_is_not_a_percentage(db):
    """Dividing by a zero baseline is the same trap the digest's velocity table
    and Mode Push momentum both had to be fixed for."""
    add(db, day="2026-08-10", region="AU", n=5)
    add(db, day="2026-08-17", region="PNG", n=5)

    assert build_dashboard_payload(db)["change"]["png"] is None


# ---------- it reports what it has ----------


def test_coverage_says_how_many_collections_there_actually_are(db):
    for day in ("2026-08-03", "2026-08-10", "2026-08-17"):
        add(db, day=day, n=2)

    coverage = build_dashboard_payload(db)["coverage"]
    assert coverage == {"collections": 3, "from": "2026-08-03", "to": "2026-08-17"}


def test_the_chart_is_capped_but_coverage_reports_the_whole_history(db):
    """The chart stays legible; the header still says how much exists."""
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    for i in range(TREND_COLLECTIONS + 4):
        add(db, day=(start + timedelta(days=7 * i)).date().isoformat(), n=1)

    payload = build_dashboard_payload(db)
    assert len(payload["collections"]) == TREND_COLLECTIONS
    assert payload["coverage"]["collections"] == TREND_COLLECTIONS + 4


def test_an_empty_database_reports_emptiness_rather_than_a_shape(db):
    """A dashboard that invents a series when it has none is how the page this
    replaces came to show 847 Australian roles a week."""
    payload = build_dashboard_payload(db)

    assert payload["collections"] == []
    assert payload["latest"] is None
    assert payload["coverage"]["collections"] == 0


def test_an_unreadable_database_is_empty_not_an_error(db, monkeypatch):
    import api.dashboard_service as mod

    def _broken(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mod, "connect", _broken)
    assert build_dashboard_payload(db)["latest"] is None


def test_the_watchlist_survives_an_empty_signals_table(db):
    """The watchlist is not a fact about collections. A fresh database with
    twenty companies and no scrapes yet should say twenty, not zero."""
    payload = build_dashboard_payload(db)

    assert payload["collections"] == []
    assert payload["watchlist"]["total"] == 2


# ---------- what the collection was made of ----------


def add_cat(db, *, day: str, category: str, n: int = 1, company="BHP", tier="A", new=0):
    with connect(db) as conn:
        for i in range(n):
            sid = f"{day}-{category}-{company}-{i}"
            conn.execute(
                "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
                "captured_at, geography, region, sector, company_name, watchlist_tier, "
                "signal_category, review_cycle, raw_content, analysis_notes, is_new_prospect, "
                "classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, "job_board", "seek", f"https://x/{sid}", f"{day}T00:41:05+00:00",
                 "AU", "AU", "mining", company, tier, category, "weekly",
                 "hiring", "note", new, f"{day}T00:41:05+00:00"),
            )


def test_the_three_groups_are_always_present_and_ordered(db):
    """A group with nothing in it is still returned. Dropping it would make a
    week with no decision points look like a week where nobody asked."""
    add_cat(db, day="2026-08-17", category="hiring_velocity", n=5)

    groups = build_dashboard_payload(db)["groups"]

    assert [g["key"] for g in groups] == ["acting", "routine", "context"]
    assert [g["count"] for g in groups] == [0, 5, 0]


def test_categories_are_grouped_by_what_they_say_about_acting(db):
    add_cat(db, day="2026-08-17", category="project", n=2)
    add_cat(db, day="2026-08-17", category="leadership", n=1)
    add_cat(db, day="2026-08-17", category="hiring_velocity", n=6)
    add_cat(db, day="2026-08-17", category="market_intel", n=1)

    groups = {g["key"]: g["count"] for g in build_dashboard_payload(db)["groups"]}

    assert groups == {"acting": 3, "routine": 6, "context": 1}


def test_shares_are_of_the_collection_not_of_each_other(db):
    add_cat(db, day="2026-08-17", category="project", n=1)
    add_cat(db, day="2026-08-17", category="hiring_velocity", n=3)

    p = build_dashboard_payload(db)

    assert p["latest"]["total"] == 4
    assert {g["key"]: g["share"] for g in p["groups"]}["acting"] == 25.0


# ---------- who was active ----------


def test_the_most_active_companies_are_ranked(db):
    add_cat(db, day="2026-08-17", category="hiring_velocity", n=4, company="BHP")
    add_cat(db, day="2026-08-17", category="hiring_velocity", n=2, company="Newmont",
            tier=None, new=1)

    companies = build_dashboard_payload(db)["companies"]

    assert [c["name"] for c in companies] == ["BHP", "Newmont"]
    assert companies[0]["count"] == 4
    assert companies[0]["tier"] == "A"
    assert companies[1]["tier"] is None and companies[1]["isNew"] is True


def test_an_unidentified_employer_is_not_listed_as_a_company(db):
    """'Unknown' is what the classifier emits when it could not name the
    employer. Offering it among the most active companies would be offering a
    name nobody can act on."""
    add_cat(db, day="2026-08-17", category="hiring_velocity", n=9, company="Unknown", tier=None)
    add_cat(db, day="2026-08-17", category="hiring_velocity", n=1, company="BHP")

    names = [c["name"] for c in build_dashboard_payload(db)["companies"]]

    assert names == ["BHP"]


def test_watchlist_coverage_counts_distinct_companies_seen(db):
    """Four signals from one watchlist company is one company seen, not four."""
    add_cat(db, day="2026-08-17", category="hiring_velocity", n=4, company="BHP")

    wl = build_dashboard_payload(db)["watchlist"]

    assert wl["total"] == 2
    assert wl["seen"] == 1
    assert wl["seenShare"] == 50.0


def test_new_names_counts_companies_not_signals(db):
    add_cat(db, day="2026-08-17", category="project", n=3, company="Civmec",
            tier=None, new=1)

    assert build_dashboard_payload(db)["newNames"] == 1


# ---------- where it came from ----------


def test_sources_count_what_they_collected_including_unclassified(db):
    """A row awaiting classification was still collected by its source. Counting
    only classified rows would understate a scraper that ran fine."""
    add(db, day="2026-08-17", n=3)
    add(db, day="2026-08-17", n=2, classified=False)

    sources = build_dashboard_payload(db)["sources"]

    assert sum(s["count"] for s in sources) == 5
    assert build_dashboard_payload(db)["latest"]["total"] == 3, "the headline stays classified-only"


def test_a_collector_is_listed_once_even_with_mixed_signal_types(db):
    """Grouping by source_type as well as source_name split one collector into
    two rows, which reads as two sources rather than one."""
    with connect(db) as conn:
        for i, kind in enumerate(("job_board", "news", "job_board")):
            conn.execute(
                "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
                "captured_at, geography, region, sector, company_name, watchlist_tier, "
                "signal_category, review_cycle, raw_content, analysis_notes, is_new_prospect, "
                "classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"mix{i}", kind, "adzuna", f"https://x/mix{i}", "2026-08-17T00:41:05+00:00",
                 "AU", "AU", "mining", "BHP", "A", "hiring_velocity", "weekly",
                 "hiring", "note", 0, "2026-08-17T00:41:05+00:00"),
            )

    sources = build_dashboard_payload(db)["sources"]

    assert [s["name"] for s in sources] == ["adzuna"]
    assert sources[0]["count"] == 3
