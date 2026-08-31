"""Tests for the dashboard payload window (api.digest_service).

The page is labelled "Weekly Digest", so it must actually be windowed — and when
the window is empty it must say so rather than presenting older signals as this
week's. These cover all three outcomes.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, time, timedelta, timezone

import pytest

from api.digest_service import MAX_SIGNALS_SHOWN, build_digest_payload
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


def _add(db, signal_id: str, *, days_ago: float, company="BHP", tier="A",
         source_type="job_board", source="seek", is_new=0):
    captured = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, sector, company_name, watchlist_tier, "
            "signal_category, review_cycle, raw_content, analysis_notes, "
            "is_new_prospect, classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, source_type, source, f"https://x/{signal_id}", captured, "AU",
             "mining", company, tier, "hiring_velocity", "weekly",
             f"{company} is hiring a Maintenance Planner in the Pilbara", "note", is_new,
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


def test_the_collection_summary_counts_only_the_window(db):
    """Regression: the tile read 'ROLES DETECTED · 7D' but was computed over
    every classified row ever, so it silently over-reported."""
    _add(db, "in-1", days_ago=1)
    _add(db, "in-2", days_ago=2)
    for i in range(5):
        _add(db, f"out-{i}", days_ago=40 + i)

    c = build_digest_payload(db, days=7)["collection"]
    assert c["collected"] == 2


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
    for key in ("sourceMode", "windowDays", "windowEmpty", "collection", "signals", "velocity"):
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
    'this week' and the baseline vanished.

    The two runs are placed at fixed wall-clock times rather than at offsets
    from `now`. An earlier version used `days_ago=7.98`, which drifts across the
    midnight boundary as the day advances — the test passed in the morning and
    failed at 23:49.
    """
    now = datetime.now(timezone.utc)
    midnight = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    this_run = midnight - timedelta(days=1) + timedelta(hours=23, minutes=14)
    prior_run = this_run - timedelta(days=7) + timedelta(minutes=26)  # 26 min later in its day

    since = lambda ts: (now - ts).total_seconds() / 86400.0
    _week(db, days_ago=since(this_run), count=4, prefix="this")
    _week(db, days_ago=since(prior_run), count=6, prefix="prior")

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


def test_competitors_are_excluded_from_new_names(db, tmp_path, monkeypatch):
    competitor_file = tmp_path / "competitors.json"
    competitor_file.write_text(
        json.dumps(
            [
                "PeopleConnexion",
                "Kiwi Niugini Recruitment",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "api.digest_service.COMPETITORS_PATH",
        competitor_file,
    )

    captured = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with connect(db) as conn:
        for signal_id, company in [
            ("competitor-1", "PeopleConnexion"),
            ("competitor-2", "Kiwi Niugini Recruitment"),
            ("prospect-1", "Example Mining Services"),
        ]:
            conn.execute(
                "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
                "captured_at, geography, sector, company_name, watchlist_tier, "
                "signal_category, review_cycle, raw_content, analysis_notes, "
                "is_new_prospect, classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    signal_id,
                    "job_board",
                    "seek",
                    f"https://x/{signal_id}",
                    captured,
                    "AU",
                    "mining",
                    company,
                    None,
                    "hiring_velocity",
                    "weekly",
                    f"{company} is hiring",
                    "note",
                    1,
                    captured,
                ),
            )

    payload = build_digest_payload(db, days=7)

    names = {
        item["co"].strip().casefold()
        for item in payload["newNames"]
    }

    assert "peopleconnexion" not in names
    assert "kiwi niugini recruitment" not in names
    assert "example mining services" in names


def test_the_shipped_competitor_file_is_valid(db):
    """The test above points the loader at a temp file, so it never touches the
    real one. If `config/competitors.json` were renamed, moved or left with a
    trailing comma, every other test would still pass and the filter would
    silently do nothing in production."""
    from api.digest_service import COMPETITORS_PATH, _competitor_names

    assert COMPETITORS_PATH.exists(), f"{COMPETITORS_PATH} is missing"
    names = _competitor_names(COMPETITORS_PATH)
    assert names, "the shipped competitor list is empty"
    assert all(n == n.casefold() for n in names), "names must be casefolded for matching"


def test_competitor_matching_is_exact_not_substring(db, tmp_path, monkeypatch):
    """"Newcrest" must not be filtered because some agency is called "Crest".
    An over-broad match here silently hides real prospects, which is worse than
    the problem it solves."""
    competitor_file = tmp_path / "competitors.json"
    competitor_file.write_text(json.dumps(["Crest Recruitment"]), encoding="utf-8")
    monkeypatch.setattr("api.digest_service.COMPETITORS_PATH", competitor_file)

    captured = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(db) as conn:
        for signal_id, company in [("a", "Crest Recruitment"), ("b", "Newcrest")]:
            conn.execute(
                "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
                "captured_at, geography, sector, company_name, watchlist_tier, "
                "signal_category, review_cycle, raw_content, analysis_notes, "
                "is_new_prospect, classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (signal_id, "job_board", "seek", f"https://x/{signal_id}", captured,
                 "AU", "mining", company, None, "hiring_velocity", "weekly",
                 f"{company} is hiring", "note", 1, captured),
            )

    names = {n["co"] for n in build_digest_payload(db, days=7)["newNames"]}
    assert "Crest Recruitment" not in names
    assert "Newcrest" in names, "an exact-name filter must not swallow a real company"


def test_a_missing_competitor_file_does_not_break_the_digest(db, tmp_path, monkeypatch):
    """Better an unfiltered digest than no digest."""
    monkeypatch.setattr("api.digest_service.COMPETITORS_PATH", tmp_path / "nope.json")
    assert build_digest_payload(db, days=7)["sourceMode"] in {"live", "synthetic"}



# ---------- the collection summary ----------
#
# Four KPI tiles used to sit here. Two showed the same variable under different
# labels, one of those contradicted the "Key Signals" list below it, and a
# fourth reported Mode Push as a hardcoded zero. These pin the replacements to
# things that are actually counted.


def test_a_news_article_is_not_counted_as_a_role(db):
    """The headline used to read "roles detected" over every signal, including
    Mining.com.au articles, which advertise no role at all."""
    _add(db, "job-1", days_ago=1)
    _add(db, "job-2", days_ago=1.1)
    _add(db, "news-1", days_ago=1.2, source_type="news", source="newsfeed")

    c = build_digest_payload(db, days=7)["collection"]
    assert c["jobs"] == 2
    assert c["news"] == 1
    assert c["collected"] == 3, "the total still covers everything gathered"


def test_the_kinds_always_add_up_to_the_total(db):
    for i in range(4):
        _add(db, f"j{i}", days_ago=1 + i * 0.1)
    for i in range(3):
        _add(db, f"n{i}", days_ago=2 + i * 0.1, source_type="news", source="newsfeed")

    c = build_digest_payload(db, days=7)["collection"]
    assert c["jobs"] + c["news"] == c["collected"]


def test_the_regions_always_add_up_to_the_total(db):
    """The split is drawn as a proportional bar, so a shortfall would render as
    a silently truncated bar rather than an obvious error."""
    for i in range(5):
        _add(db, f"s{i}", days_ago=1 + i * 0.1)

    c = build_digest_payload(db, days=7)["collection"]
    assert c["regions"]["AU"] + c["regions"]["PNG"] == c["collected"]


def test_shown_matches_the_list_the_page_actually_renders(db):
    """The old 'Key signals' tile showed every signal collected while the
    section directly beneath it listed 40. One of the two had to be wrong."""
    for i in range(MAX_SIGNALS_SHOWN + 12):
        _add(db, f"s{i}", days_ago=1 + i * 0.01, company=f"Co {i}")

    p = build_digest_payload(db, days=7)
    assert p["collection"]["shown"] == len(p["signals"])
    assert p["collection"]["shown"] == MAX_SIGNALS_SHOWN
    assert p["collection"]["collected"] > p["collection"]["shown"], "shown is a subset"


def test_the_source_count_is_measured_not_assumed(db):
    _add(db, "a", days_ago=1, source="seek")
    _add(db, "b", days_ago=1.1, source="seek")
    _add(db, "c", days_ago=1.2, source="pngworkforce")

    assert build_digest_payload(db, days=7)["collection"]["sources"] == 2


def test_new_names_counts_unfamiliar_companies_once_each(db):
    _add(db, "n1", days_ago=1, company="Newco", tier=None, is_new=1)
    _add(db, "n2", days_ago=1.1, company="Newco", tier=None, is_new=1)
    _add(db, "n3", days_ago=1.2, company="Otherco", tier=None, is_new=1)
    _add(db, "known", days_ago=1.3, company="BHP", tier="A", is_new=0)

    assert build_digest_payload(db, days=7)["collection"]["newNames"] == 2


def test_nothing_reports_mode_push_activity(db):
    """It was a hardcoded zero that would have stayed zero after the first
    profile was saved. The digest is about market signals; Mode Push has its
    own page."""
    _add(db, "s1", days_ago=1)
    payload = build_digest_payload(db, days=7)
    assert "pushQueries" not in payload["collection"]
    assert "push" not in str(payload["collection"]).lower()


def _add_at(db, signal_id: str, captured: str, company="BHP", tier="A"):
    """Like `_add`, but at an exact instant rather than a number of days back."""
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, region, sector, company_name, watchlist_tier, "
            "signal_category, review_cycle, raw_content, analysis_notes, "
            "is_new_prospect, classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, "job_board", "seek", f"https://x/{signal_id}", captured, "AU",
             "AU", "mining", company, tier, "hiring_velocity", "weekly",
             f"{company} is hiring a Maintenance Planner", "note", 0, captured),
        )


# ---------- the window is day-aligned ----------
#
# Regression: the boundary was `now - days`, to the second. Scrapes never start
# at the same minute, so a run sitting almost exactly `days` back was included
# or excluded depending on when the digest happened to be built. On 25 August
# the previous week's 146 signals cleared the line by 24 seconds — a digest
# generated a minute later would have reported different headline figures from
# identical data.


def _at(ts):
    return ts.isoformat(timespec="seconds")


def test_the_window_starts_at_midnight_not_at_the_current_time(db):
    from api.digest_service import _day_after

    boundary = _day_after(datetime.now(timezone.utc)) - timedelta(days=7)
    assert (boundary.hour, boundary.minute, boundary.second) == (0, 0, 0)


def test_seconds_do_not_decide_whether_a_run_is_in_the_window(db):
    """Two signals a few seconds either side of the boundary must not land on
    opposite sides of it — that was the whole bug."""
    from api.digest_service import _day_after

    boundary = _day_after(datetime.now(timezone.utc)) - timedelta(days=7)
    _add_at(db, "just-before", _at(boundary - timedelta(seconds=30)))
    _add_at(db, "just-after", _at(boundary + timedelta(seconds=30)))

    ids = {s["id"] for s in build_digest_payload(db, days=7)["signals"]}
    assert "just-after" in ids
    assert "just-before" not in ids, "a signal from the prior period leaked in"


def test_a_run_exactly_a_week_back_is_a_previous_week(db):
    """The case that actually happened: a weekly pipeline run seven days ago is
    last week's digest, not this week's, whatever the minute says."""
    now = datetime.now(timezone.utc)
    _add_at(db, "last-week", _at(now - timedelta(days=7)))
    _add_at(db, "this-week", _at(now - timedelta(minutes=5)))

    p = build_digest_payload(db, days=7)
    ids = {s["id"] for s in p["signals"]}
    assert ids == {"this-week"}
    assert p["collection"]["collected"] == 1, "two runs were counted as one week"


def test_the_capture_time_reaches_the_row(db):
    """So a reader can tell a posting found in this run from one carried over
    from an earlier run inside the same window."""
    when = _at(datetime.now(timezone.utc) - timedelta(hours=2))
    _add_at(db, "s1", when)

    (signal,) = build_digest_payload(db, days=7)["signals"]
    assert signal["capturedAt"] == when
