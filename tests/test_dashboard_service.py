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
