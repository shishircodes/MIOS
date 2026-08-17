"""Tests for the dashboard payload window (api.digest_service).

The page is labelled "Weekly Digest", so it must actually be windowed — and when
the window is empty it must say so rather than presenting older signals as this
week's. These cover all three outcomes.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from api.digest_service import build_digest_payload
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
    path = tmp_path / "digest.db"
    init_db(path, watchlist_path=watchlist)
    return path


def _add(db, signal_id: str, *, days_ago: float, company="BHP", tier="A"):
    captured = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, sector, company_name, watchlist_tier, "
            "signal_category, review_cycle, raw_content, analysis_notes, "
            "is_new_prospect, classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, "job_board", "seek", f"https://x/{signal_id}", captured, "AU",
             "mining", company, tier, "hiring_velocity", "weekly",
             f"{company} is hiring a Maintenance Planner in the Pilbara", "note", 0,
             captured),
        )


# ---------- outcome 1: signals inside the window ----------


def test_recent_signals_are_reported_as_in_window(db):
    _add(db, "recent-1", days_ago=1)
    _add(db, "recent-2", days_ago=3)

    p = build_digest_payload(db, days=7)
    assert p["sourceMode"] == "live"
    assert p["windowEmpty"] is False
    assert p["windowDays"] == 7
    assert len(p["signals"]) == 2


def test_signals_outside_the_window_are_excluded(db):
    _add(db, "recent", days_ago=2)
    _add(db, "old", days_ago=30)

    p = build_digest_payload(db, days=7)
    assert p["windowEmpty"] is False
    ids = {s["id"] for s in p["signals"]}
    assert ids == {"recent"}, "a 30-day-old signal must not appear in a 7-day window"


def test_kpis_count_only_the_window(db):
    """Regression: the KPI tile read 'ROLES DETECTED · 7D' but was computed over
    every classified row ever, so it silently over-reported."""
    _add(db, "in-1", days_ago=1)
    _add(db, "in-2", days_ago=2)
    for i in range(5):
        _add(db, f"out-{i}", days_ago=40 + i)

    p = build_digest_payload(db, days=7)
    assert p["kpis"]["rolesThisWeek"]["val"] == 2
    assert p["kpis"]["newSignals"]["val"] == 2


def test_window_length_is_configurable(db):
    _add(db, "d10", days_ago=10)
    assert build_digest_payload(db, days=7)["windowEmpty"] is True
    p30 = build_digest_payload(db, days=30)
    assert p30["windowEmpty"] is False
    assert p30["windowDays"] == 30


# ---------- outcome 2: empty window, older data exists ----------


def test_empty_window_falls_back_to_latest_and_flags_it(db):
    _add(db, "old-1", days_ago=40)
    _add(db, "old-2", days_ago=50)

    p = build_digest_payload(db, days=7)
    assert p["sourceMode"] == "live"
    assert p["windowEmpty"] is True, "must flag that these are not this week's signals"
    assert len(p["signals"]) == 2, "still shows data rather than blanking the dashboard"


def test_fallback_orders_newest_first(db):
    _add(db, "older", days_ago=60)
    _add(db, "newer", days_ago=40)

    p = build_digest_payload(db, days=7)
    assert [s["id"] for s in p["signals"]] == ["newer", "older"]


# ---------- outcome 3: nothing classified at all ----------


def test_empty_database_falls_back_to_synthetic(db):
    p = build_digest_payload(db, days=7)
    assert p["sourceMode"] == "synthetic"
    # Not "windowEmpty": there is no live data to be stale, so the notice would
    # be misleading on top of the synthetic banner.
    assert p["windowEmpty"] is False
    assert len(p["signals"]) > 0


def test_missing_database_falls_back_to_synthetic(tmp_path):
    p = build_digest_payload(tmp_path / "does-not-exist.db", days=7)
    assert p["sourceMode"] == "synthetic"


# ---------- payload shape ----------


def test_payload_always_declares_the_window(db):
    _add(db, "s1", days_ago=1)
    p = build_digest_payload(db, days=7)
    for key in ("sourceMode", "windowDays", "windowEmpty", "kpis", "signals", "velocity"):
        assert key in p, f"the dashboard reads {key}"


# ---------- collection date in the heading ----------


def test_heading_names_the_scrape_date_not_today(db):
    """Regression: the heading was `datetime.now()`, so a dashboard opened a week
    after the last scrape still announced today's date as if the data were fresh."""
    _add(db, "s1", days_ago=4)

    p = build_digest_payload(db, days=7)
    expected = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%d %B %Y").lstrip("0")
    assert p["weekLabel"] == f"Week of {expected}"
    assert datetime.now(timezone.utc).strftime("%d %B %Y").lstrip("0") not in p["weekLabel"]


def test_heading_shows_a_range_when_collection_spans_days(db):
    _add(db, "early", days_ago=5)
    _add(db, "late", days_ago=1)

    label = build_digest_payload(db, days=7)["weekLabel"]
    assert "–" in label, f"expected a date range, got {label!r}"
    assert not label.startswith("Week of")


def test_collection_span_is_exposed(db):
    _add(db, "a", days_ago=3)
    _add(db, "b", days_ago=1)

    p = build_digest_payload(db, days=7)
    assert p["collectedFrom"] < p["collectedTo"]
    assert p["collectedTo"].endswith("+00:00")


def test_synthetic_rows_have_no_collection_date(db):
    # Empty database -> synthetic fallback, which carries no captured_at.
    p = build_digest_payload(db, days=7)
    assert p["sourceMode"] == "synthetic"
    assert p["collectedFrom"] is None
    assert p["weekLabel"] == "Sample dataset"


def test_generated_at_is_still_now(db):
    """`generatedAt` legitimately means "when this page was built" and must not
    be confused with the collection date."""
    _add(db, "s1", days_ago=4)
    p = build_digest_payload(db, days=7)
    assert datetime.now(timezone.utc).strftime("%d %b %Y") in p["generatedAt"]


# ---------- region balance under the display cap ----------


def _add_region(db, signal_id, *, region, days_ago=1, category="hiring_velocity"):
    """PNG geography is inferred from raw_content keywords, not a column."""
    captured = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    raw = ("Process Operator at Lihir gold mine, Papua New Guinea" if region == "PNG"
           else "Maintenance Planner at BHP Newman WA Australia")
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, sector, company_name, watchlist_tier, "
            "signal_category, review_cycle, raw_content, analysis_notes, "
            "is_new_prospect, classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, "job_board", "seek", f"https://x/{signal_id}", captured, region,
             "mining", "BHP", "A", category, "weekly", raw, "note", 0, captured),
        )


def test_display_cap_never_drops_a_whole_region(db):
    """Regression: the two scrapers finish a second apart, so ordering by
    captured_at DESC put every SEEK row ahead of every PNGworkforce row. The
    `[:40]` cut landed inside SEEK and Papua New Guinea vanished entirely."""
    # 50 AU captured *later*, 50 PNG captured earlier — the exact shape that broke it.
    for i in range(50):
        _add_region(db, f"png-{i}", region="PNG", days_ago=2)
    for i in range(50):
        _add_region(db, f"au-{i}", region="AU", days_ago=1)

    p = build_digest_payload(db, days=7)
    regions = {s["region"] for s in p["signals"]}
    assert regions == {"AU", "PNG"}, f"a region was dropped by the cap: {regions}"

    counts = Counter(s["region"] for s in p["signals"])
    assert counts["AU"] == counts["PNG"] == 20, counts


def test_stronger_categories_are_shown_first(db):
    for i in range(5):
        _add_region(db, f"noise-{i}", region="AU", category="hiring_velocity")
    _add_region(db, "boss", region="AU", category="leadership")

    p = build_digest_payload(db, days=7)
    assert p["signals"][0]["category"] == "leadership"


def test_a_region_with_fewer_signals_still_gets_all_of_them(db):
    """Round-robin must not cap the smaller region at half the quota."""
    for i in range(30):
        _add_region(db, f"au-{i}", region="AU")
    for i in range(3):
        _add_region(db, f"png-{i}", region="PNG")

    p = build_digest_payload(db, days=7)
    counts = Counter(s["region"] for s in p["signals"])
    assert counts["PNG"] == 3, "all PNG signals should appear"
    assert counts["AU"] == 30, "AU should take the remaining slots"


def test_display_numbers_follow_the_shown_order(db):
    for i in range(4):
        _add_region(db, f"au-{i}", region="AU")
    p = build_digest_payload(db, days=7)
    assert [s["n"] for s in p["signals"]] == ["01", "02", "03", "04"]


# ---------- region derivation + source balance ----------


def _add_full(db, signal_id, *, source, geography, raw, days_ago=1,
              category="hiring_velocity", tier=None):
    captured = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, sector, company_name, watchlist_tier, "
            "signal_category, review_cycle, raw_content, analysis_notes, "
            "is_new_prospect, classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, "job_board", source, f"https://x/{signal_id}", captured, geography,
             "mining", "Acme", tier, category, "weekly", raw, "note", 0, captured),
        )


def test_source_geography_wins_when_content_names_no_landmark(db):
    """Regression: region came from keyword inference alone, so PNGworkforce jobs
    whose teaser mentioned no PNG place were filed under Australia."""
    _add_full(db, "png-generic", source="pngworkforce", geography="PNG",
              raw="Administrative Assistant | Acme | full time office role")

    p = build_digest_payload(db, days=7)
    assert p["signals"][0]["region"] == "PNG"


def test_content_can_still_promote_an_au_row_to_png(db):
    """A PNG role advertised on an Australian board is a PNG signal — the source's
    own geography is a baseline, not a ceiling."""
    _add_full(db, "seek-png", source="seek", geography="AU",
              raw="Project Engineers to work in PNG on civil projects | Kiwi Niugini")

    p = build_digest_payload(db, days=7)
    assert p["signals"][0]["region"] == "PNG"


def test_au_rows_stay_au(db):
    _add_full(db, "seek-au", source="seek", geography="AU",
              raw="Maintenance Planner | BHP | Newman WA")
    p = build_digest_payload(db, days=7)
    assert p["signals"][0]["region"] == "AU"


def test_verbose_source_cannot_monopolise_a_region(db):
    """Regression: the sort tiebreak was content length, so Adzuna's ~610-char
    records displaced every SEEK record at ~245 and SEEK vanished from the
    Australia section despite having the most AU signals."""
    for i in range(30):
        _add_full(db, f"adz-{i}", source="adzuna", geography="AU",
                  raw="Engineer at Acme in Perth. " + ("detail " * 90))
    for i in range(30):
        _add_full(db, f"seek-{i}", source="seek", geography="AU",
                  raw="Engineer at Acme in Perth. short")

    p = build_digest_payload(db, days=7)
    sources = Counter(s["source"] for s in p["signals"])
    assert sources["seek"] > 0, f"the terser source was squeezed out: {sources}"
    assert sources["adzuna"] > 0
    # Round-robin, so the split is even.
    assert abs(sources["adzuna"] - sources["seek"]) <= 1, sources


def test_watchlist_tier_outranks_a_longer_advert(db):
    _add_full(db, "wordy", source="seek", geography="AU", tier=None,
              raw="Engineer at Acme. " + ("padding " * 60))
    _add_full(db, "tier-a", source="seek", geography="AU", tier="A",
              raw="Engineer at Acme. short")

    p = build_digest_payload(db, days=7)
    assert p["signals"][0]["id"] == "tier-a"


# ---------- hiring velocity baseline ----------
#
# The baseline used to be `round(wk * 0.7)`, so the change was always ~+43% —
# derived from this week's own number rather than measured against history.


def _week(db, *, days_ago, count, company="BHP", prefix=None):
    """`count` watchlist signals for one company, all captured `days_ago`."""
    tag = prefix or f"{company.lower().replace(' ', '')}-{days_ago}"
    for i in range(count):
        _add(db, f"{tag}-{i}", days_ago=days_ago, company=company)


def _row(payload, company):
    return next(r for r in payload["velocity"] if r["co"] == company)


def test_baseline_is_measured_from_earlier_windows(db):
    _week(db, days_ago=1, count=10)    # this window
    _week(db, days_ago=9, count=4)     # one window back
    _week(db, days_ago=16, count=6)    # two windows back

    r = _row(build_digest_payload(db, days=7), "BHP")
    assert r["wk"] == 10
    assert r["avg"] == 5.0, "average of the two prior windows, not 70% of 10"
    assert r["change"] == 100
    assert r["basis"] == 2


def test_baseline_is_not_a_fraction_of_this_week(db):
    """The tell of the old formula: change was ~+43% whatever the numbers."""
    _week(db, days_ago=1, count=10)
    _week(db, days_ago=9, count=10)

    r = _row(build_digest_payload(db, days=7), "BHP")
    assert r["change"] == 0, "flat activity must read as flat"


def test_a_decline_is_reported_as_a_decline(db):
    _week(db, days_ago=1, count=3)
    _week(db, days_ago=9, count=12)

    r = _row(build_digest_payload(db, days=7), "BHP")
    assert r["change"] == -75


def test_no_history_reports_no_baseline_rather_than_inventing_one(db):
    _week(db, days_ago=1, count=7)

    r = _row(build_digest_payload(db, days=7), "BHP")
    assert r["avg"] is None
    assert r["change"] is None
    assert r["basis"] == 0


def test_a_company_new_this_window_has_no_percentage(db):
    """History exists, but not for this company — there is nothing to be a
    percentage of, so the change is undefined rather than infinite."""
    _week(db, days_ago=9, count=5, company="BHP")
    _week(db, days_ago=1, count=4, company="BHP")
    _week(db, days_ago=1, count=3, company="Newmont")

    r = _row(build_digest_payload(db, days=7), "Newmont")
    assert r["avg"] == 0.0
    assert r["change"] is None
    assert r["basis"] == 1


def test_windows_with_no_pipeline_run_do_not_count_as_zero(db):
    """A week nobody scraped is not a week nobody hired. Counting it would halve
    the average and manufacture a rise that never happened."""
    _week(db, days_ago=1, count=6)
    _week(db, days_ago=9, count=6)
    # Nothing at all 15-28 days ago: the pipeline did not run.

    r = _row(build_digest_payload(db, days=7), "BHP")
    assert r["basis"] == 1, "only the window that actually ran should count"
    assert r["avg"] == 6.0
    assert r["change"] == 0


def test_a_quiet_company_in_a_window_that_ran_does_count_as_zero(db):
    """The distinction above is about whether the *pipeline* ran, not whether
    this particular company appeared."""
    _week(db, days_ago=16, count=4, company="Newmont")   # ran, no BHP
    _week(db, days_ago=9, count=4, company="BHP")
    _week(db, days_ago=1, count=4, company="BHP")

    r = _row(build_digest_payload(db, days=7), "BHP")
    assert r["basis"] == 2
    assert r["avg"] == 2.0, "the window BHP sat out is a real zero"


def test_trend_series_is_real_counts(db):
    """The sparkline was `[avg, avg*1.1, avg*0.95, avg*1.05, wk]` — decoration
    shaped like data."""
    _week(db, days_ago=16, count=2)
    _week(db, days_ago=9, count=8)
    _week(db, days_ago=1, count=5)

    r = _row(build_digest_payload(db, days=7), "BHP")
    assert r["trend"] == [2, 8, 5], "oldest first, ending with this window"


def test_baseline_looks_back_no_further_than_the_window_count(db):
    _week(db, days_ago=1, count=4)
    for w in range(1, 7):  # six prior windows; only four may be used
        _week(db, days_ago=2 + w * 7, count=4)

    assert _row(build_digest_payload(db, days=7), "BHP")["basis"] == 4


def test_this_week_excludes_older_rows_when_the_window_is_empty(db):
    """With an empty window the payload falls back to older signals. The velocity
    column says 'this week', so it must still mean one window — not everything."""
    _week(db, days_ago=30, count=5)
    _week(db, days_ago=40, count=9)

    p = build_digest_payload(db, days=7)
    assert p["windowEmpty"] is True
    assert _row(p, "BHP")["wk"] == 5, "only the most recent window's signals"


def test_a_scrape_that_started_later_in_the_day_still_counts_as_a_prior_week(db):
    """Regression, found against live data: the two runs started at 23:14 and
    23:40. Anchoring the window on the exact timestamp put the boundary 26
    minutes before the earlier scrape, so a full prior week was absorbed into
    'this week' and the baseline vanished."""
    now = datetime.now(timezone.utc)
    # Prior week's run started 26 minutes later in the day than this week's.
    _week(db, days_ago=1 + 0.02, count=4)   # this week, earlier in the day
    _week(db, days_ago=8 - 0.02, count=6)   # a week back, later in the day

    r = _row(build_digest_payload(db, days=7), "BHP")
    assert r["basis"] == 1, "the earlier week was swallowed by the current window"
    assert r["wk"] == 4
    assert r["avg"] == 6.0


def test_synthetic_rows_get_no_baseline(db):
    for r in build_digest_payload(db, days=7)["velocity"]:
        assert r["basis"] == 0
        assert r["change"] is None


def test_velocity_rows_always_declare_their_basis(db):
    _week(db, days_ago=1, count=3)
    for r in build_digest_payload(db, days=7)["velocity"]:
        for key in ("co", "wk", "avg", "change", "basis", "trend", "sector", "tier"):
            assert key in r, f"the velocity table reads {key}"
