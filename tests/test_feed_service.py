"""Tests for the Signal Feed payload (api.digest_service.build_feed_payload).

The feed is the browse-everything view: unwindowed, unranked, uncapped. The
digest is a selection from it. These pin the differences, and the pagination
arithmetic the UI depends on.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from api.digest_service import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, build_feed_payload
from loader.db import connect
from loader.ingest import init_db


@pytest.fixture
def watchlist(tmp_path):
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    return p


@pytest.fixture
def db(tmp_path, watchlist):
    path = tmp_path / "feed.db"
    init_db(path, watchlist_path=watchlist)
    return path


def _add(db, signal_id, *, days_ago=1, company="BHP", geo="AU", cycle="weekly",
         source="seek", raw=None, classified=True):
    captured = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, sector, company_name, watchlist_tier, "
            "signal_category, review_cycle, raw_content, analysis_notes, "
            "is_new_prospect, classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, "job_board", source, f"https://x/{signal_id}", captured, geo,
             "mining", company, "A", "hiring_velocity", cycle,
             raw or f"{company} is hiring a Maintenance Planner", "note", 0,
             captured if classified else None),
        )


# ---------- the whole list, not a window ----------


def test_returns_every_classified_signal_regardless_of_age(db):
    """The digest is windowed to a week; the feed is not. A six-month-old signal
    still belongs in a list called 'all signals'."""
    _add(db, "recent", days_ago=1)
    _add(db, "ancient", days_ago=180)

    p = build_feed_payload(db)
    assert p["total"] == 2
    assert {s["id"] for s in p["signals"]} == {"recent", "ancient"}


def test_is_not_capped_at_the_digest_display_limit(db):
    """Regression: the feed reused the digest payload, so it showed 40 rows no
    matter how many existed."""
    for i in range(120):
        _add(db, f"s-{i:03d}")

    p = build_feed_payload(db, limit=MAX_PAGE_SIZE)
    assert p["total"] == 120
    assert len(p["signals"]) == 120


def test_newest_first(db):
    _add(db, "older", days_ago=10)
    _add(db, "newer", days_ago=1)
    assert [s["id"] for s in build_feed_payload(db)["signals"]] == ["newer", "older"]


# ---------- counts ----------


def test_scraped_all_time_includes_unclassified_rows(db):
    """'Signals collected all time' means what the scrapers fetched, which is
    not the same as what Gemini has read."""
    _add(db, "done", classified=True)
    _add(db, "pending", classified=False)

    p = build_feed_payload(db)
    assert p["scrapedAllTime"] == 2
    assert p["totalClassified"] == 1, "the list itself only shows classified rows"
    assert p["total"] == 1


def test_total_reflects_the_filter_but_total_classified_does_not(db):
    for i in range(5):
        _add(db, f"au-{i}", geo="AU")
    for i in range(3):
        _add(db, f"png-{i}", geo="PNG", raw="Operator at Lihir, Papua New Guinea")

    p = build_feed_payload(db, region="PNG")
    assert p["total"] == 3, "pagination walks the filtered set"
    assert p["totalClassified"] == 8, "the unfiltered figure stays available"


# ---------- pagination ----------


def test_pages_do_not_overlap_and_cover_everything(db):
    for i in range(25):
        _add(db, f"s-{i:02d}", days_ago=i + 1)

    seen: list[str] = []
    for offset in (0, 10, 20):
        page = build_feed_payload(db, limit=10, offset=offset)["signals"]
        seen.extend(s["id"] for s in page)

    assert len(seen) == 25
    assert len(set(seen)) == 25, "a row appeared on two pages"


def test_row_numbers_are_absolute_not_per_page(db):
    """Row 51 should read 51 on page two, not 01."""
    for i in range(60):
        _add(db, f"s-{i:02d}", days_ago=i + 1)

    page2 = build_feed_payload(db, limit=50, offset=50)["signals"]
    assert page2[0]["n"] == "51"


def test_offset_past_the_end_returns_no_rows_rather_than_failing(db):
    _add(db, "only")
    p = build_feed_payload(db, limit=50, offset=500)
    assert p["signals"] == []
    assert p["total"] == 1, "the total still describes the whole filtered set"


def test_page_size_is_clamped(db):
    _add(db, "s1")
    assert build_feed_payload(db, limit=10_000)["limit"] == MAX_PAGE_SIZE
    assert build_feed_payload(db, limit=0)["limit"] == 1
    assert build_feed_payload(db)["limit"] == DEFAULT_PAGE_SIZE


def test_negative_offset_is_treated_as_the_first_page(db):
    _add(db, "s1")
    assert build_feed_payload(db, offset=-5)["offset"] == 0


# ---------- filters ----------


def test_region_filter_uses_the_displayed_region_not_the_column(db):
    """A PNG role advertised on an Australian board is displayed as PNG, so
    filtering on the stored `geography` column would disagree with the row."""
    _add(db, "au", geo="AU")
    _add(db, "png-on-seek", geo="AU",
         raw="Project Engineers to work in PNG on civil projects | Kiwi Niugini")

    p = build_feed_payload(db, region="PNG")
    assert [s["id"] for s in p["signals"]] == ["png-on-seek"]


def test_cycle_filter(db):
    _add(db, "w", cycle="weekly")
    _add(db, "m", cycle="monthly")
    assert [s["id"] for s in build_feed_payload(db, cycle="MONTHLY")["signals"]] == ["m"]


def test_source_filter_updates_count(db):
    _add(db, "seek", source="seek")
    _add(db, "news", source="newsfeed")

    p = build_feed_payload(db, source="NEWSFEED")
    assert [s["id"] for s in p["signals"]] == ["news"]
    assert p["total"] == 1
    assert p["totalClassified"] == 2


def test_search_requires_every_term(db):
    """Both rows match "bhp"; only one also matches "pilbara"."""
    _add(db, "bhp-pilbara", company="BHP", raw="BHP Maintenance Planner Pilbara")
    _add(db, "bhp-perth", company="BHP", raw="BHP Office Administrator Perth")

    assert build_feed_payload(db, q="bhp")["total"] == 2
    narrowed = build_feed_payload(db, q="bhp pilbara")
    assert [s["id"] for s in narrowed["signals"]] == ["bhp-pilbara"], "AND, not OR"


def test_search_covers_sector_as_well_as_the_text(db):
    """Searching "mining" should find a mining-sector row even when the advert
    text never says the word — the sector is part of what the row is."""
    _add(db, "s1", raw="BHP Maintenance Planner, Newman")
    assert build_feed_payload(db, q="mining")["total"] == 1


def test_search_is_case_insensitive(db):
    _add(db, "s1", company="BHP")
    assert build_feed_payload(db, q="bhp")["total"] == 1


def test_filters_combine(db):
    _add(db, "au-weekly-seek", geo="AU", cycle="weekly", source="seek", company="BHP")
    _add(db, "au-weekly-news", geo="AU", cycle="weekly", source="newsfeed", company="BHP")
    _add(db, "au-monthly-seek", geo="AU", cycle="monthly", source="seek", company="BHP")
    _add(db, "png-weekly-seek", geo="PNG", cycle="weekly", source="seek", company="BHP",
         raw="BHP Operator at Lihir, Papua New Guinea")

    p = build_feed_payload(db, region="AU", cycle="WEEKLY", source="SEEK", q="bhp")
    assert [s["id"] for s in p["signals"]] == ["au-weekly-seek"]


def test_blank_filters_are_ignored(db):
    """The UI sends nothing for 'ALL', but an empty string must not filter
    everything out if one slips through."""
    _add(db, "s1")
    assert build_feed_payload(db, region="", cycle="", source="", q="  ")["total"] == 1


# ---------- shape ----------


def test_rows_have_the_same_shape_the_digest_produces(db):
    _add(db, "s1")
    s = build_feed_payload(db)["signals"][0]
    for key in ("id", "n", "region", "tier", "company", "title", "desc",
                "sector", "source", "category", "cycle", "conf"):
        assert key in s, f"the feed renders {key}"
    assert "_rank" not in s, "internal sort key must not reach the browser"


def test_missing_database_returns_an_empty_page_rather_than_failing(tmp_path):
    p = build_feed_payload(tmp_path / "nope.db")
    assert p["signals"] == []
    assert p["total"] == 0
    assert p["scrapedAllTime"] == 0
