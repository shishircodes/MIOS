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

from api.dashboard_service import (
    MAX_TREND_COLLECTIONS,
    TREND_COLLECTIONS,
    build_dashboard_payload,
)
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


# ---------- choosing what is shown ----------


def test_a_chosen_collection_replaces_the_latest(db):
    add(db, day="2026-08-10", region="AU", n=5)
    add(db, day="2026-08-17", region="AU", n=9)

    p = build_dashboard_payload(db, collection="2026-08-10")

    assert p["selected"] == "2026-08-10"
    assert p["latest"]["total"] == 5
    assert p["isLatest"] is False


def test_an_older_collection_is_compared_with_the_one_before_it(db):
    """Not with the newest. Looking back at an earlier week should show the
    movement that was reported at the time."""
    add(db, day="2026-08-03", region="AU", n=10)
    add(db, day="2026-08-10", region="AU", n=5)
    add(db, day="2026-08-17", region="AU", n=100)

    p = build_dashboard_payload(db, collection="2026-08-10")

    assert p["change"]["total"] == -50.0


def test_an_unknown_collection_falls_back_to_the_latest(db):
    """A dashboard is somewhere people arrive from stale links. Showing the
    current week beats an empty page or an error."""
    add(db, day="2026-08-17", region="AU", n=4)

    p = build_dashboard_payload(db, collection="1999-01-01")

    assert p["selected"] == "2026-08-17"
    assert p["isLatest"] is True


def test_the_first_collection_has_nothing_to_compare_with(db):
    add(db, day="2026-08-10", region="AU", n=5)
    add(db, day="2026-08-17", region="AU", n=9)

    p = build_dashboard_payload(db, collection="2026-08-10")

    assert p["change"]["total"] is None


# ---------- narrowing to one market ----------


def test_a_region_narrows_every_panel_and_the_headline(db):
    add(db, day="2026-08-17", region="AU", sector="mining", n=6)
    add(db, day="2026-08-17", region="PNG", sector="oil_gas", n=2)

    both = build_dashboard_payload(db)
    png = build_dashboard_payload(db, region="PNG")

    assert both["latest"]["total"] == 8
    assert png["latest"]["total"] == 2, "the headline follows the filter"
    assert [s["key"] for s in png["sectors"]] == ["oil_gas"]
    assert png["region"] == "PNG"


def test_the_trend_chart_keeps_both_series_when_a_region_is_chosen(db):
    """The chart answers whether the two markets move together. Filtering one
    out does not narrow that question, it removes it."""
    add(db, day="2026-08-10", region="AU", n=4)
    add(db, day="2026-08-10", region="PNG", n=3)
    add(db, day="2026-08-17", region="AU", n=6)
    add(db, day="2026-08-17", region="PNG", n=1)

    png = build_dashboard_payload(db, region="PNG")

    assert [(c["au"], c["png"]) for c in png["collections"]] == [(4, 3), (6, 1)]


def test_an_unknown_region_shows_both(db):
    add(db, day="2026-08-17", region="AU", n=3)

    assert build_dashboard_payload(db, region="ATLANTIS")["region"] is None


# ---------- how much of the history is charted ----------


def test_the_trend_window_caps_the_chart_but_not_the_coverage(db):
    for i in range(8):
        add(db, day=f"2026-08-{i + 1:02d}", n=2)

    p = build_dashboard_payload(db, trend=4)

    assert len(p["collections"]) == 4
    assert p["trendWindow"] == 4
    assert p["coverage"]["collections"] == 8, "the header still says what exists"


def test_the_window_is_clamped_to_something_drawable(db):
    add(db, day="2026-08-17", n=2)

    assert build_dashboard_payload(db, trend=9999)["trendWindow"] == MAX_TREND_COLLECTIONS
    assert build_dashboard_payload(db, trend=1)["trendWindow"] == 2


def test_every_collection_is_offered_newest_first(db):
    add(db, day="2026-08-10", n=5)
    add(db, day="2026-08-17", n=9)

    available = build_dashboard_payload(db)["available"]

    assert [a["date"] for a in available] == ["2026-08-17", "2026-08-10"]
    assert available[0]["total"] == 9, "the picker can say what each one holds"
