"""Tests for the dashboard payload window (api.digest_service).

The page is labelled "Weekly Digest", so it must actually be windowed — and when
the window is empty it must say so rather than presenting older signals as this
week's. These cover all three outcomes.
"""
from __future__ import annotations

import json
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
