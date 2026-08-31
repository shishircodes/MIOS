"""Tests for the admin endpoints (api.admin_api).

Source health is derived from the signals rather than a run log, so what needs
pinning is the derivation: a run that stopped at the cap, a source that has gone
quiet, one that cannot run at all.
"""
from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from api import access, admin_api
from loader.db import connect
from loader.ingest import init_db

ADMIN = {"email": "boss@easyskill.com", "role": "admin"}


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


@pytest.fixture
def db(tmp_path, monkeypatch):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    path = tmp_path / "admin.db"
    init_db(path, watchlist_path=wl)
    # Both modules resolve the database through loader.db.connect, so pointing
    # the default target at the temp file covers the endpoints too.
    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setattr("api.admin_api.connect", lambda t=None, **kw: connect(path, **kw))
    monkeypatch.setattr("api.access.connect", lambda t=None, **kw: connect(path, **kw))
    # The endpoints write through loader.source_settings, which holds its own
    # `connect` reference and resolves its own target. Without this, toggling a
    # source from a test reached whatever DATABASE_URL names.
    monkeypatch.setattr("loader.source_settings.connect",
                        lambda t=None, **kw: connect(path, **kw))
    # init_db seeds the bootstrap admin; these tests build the exact access
    # situation under test, so they start from an empty list.
    with connect(path) as conn:
        conn.execute("DELETE FROM app_users")
    return path


_seq = itertools.count()


def _add_signal(db, *, source: str, captured: str, classified: bool = True) -> None:
    n = next(_seq)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, raw_content, classified_at) VALUES (?,?,?,?,?,?,?,?)",
            (f"sig-{n}", "job_board", source, f"https://example.com/{source}/{n}",
             captured, "AU", "Fitter wanted", captured if classified else None),
        )


def _by_name(payload: dict, name: str) -> dict:
    return next(s for s in payload["sources"] if s["name"] == name)


# ---------- source health ----------


def test_a_source_that_ran_this_week_is_collecting(db):
    for i in range(3):
        _add_signal(db, source="seek", captured=_iso(1 + i * 0.01))
    seek = _by_name(admin_api.source_health(user=ADMIN), "seek")
    assert seek["status"] == "ok"
    assert seek["totalRecords"] == 3
    assert seek["last7Days"] == 3


def test_a_source_that_has_gone_quiet_is_stale(db):
    """Longer than the weekly cycle, so an ordinary week never trips it."""
    _add_signal(db, source="seek", captured=_iso(admin_api.STALE_AFTER_DAYS + 5))
    seek = _by_name(admin_api.source_health(user=ADMIN), "seek")
    assert seek["status"] == "stale"
    assert seek["last7Days"] == 0, "an old record is not recent activity"


def test_a_source_with_no_records_says_so_rather_than_showing_a_bare_zero(db):
    seek = _by_name(admin_api.source_health(user=ADMIN), "seek")
    assert seek["status"] == "never_run"
    assert seek["totalRecords"] == 0


def test_a_source_missing_its_credentials_is_reported_as_unconfigured(db, monkeypatch):
    """Otherwise Adzuna shows zero records and looks broken rather than off."""
    monkeypatch.setattr(admin_api, "settings",
                        type("S", (), {"adzuna_configured": False,
                                       "allowed_google_domain": ""})())
    adzuna = _by_name(admin_api.source_health(user=ADMIN), "adzuna")
    assert adzuna["status"] == "not_configured"
    assert "ADZUNA" in adzuna["note"]


def test_the_last_run_is_the_most_recent_day_not_the_running_total(db):
    """Three weekly runs. 'Last run' must be the newest one alone, or the page
    would suggest one scrape collected everything ever collected."""
    for day in (14.0, 7.0):
        for i in range(5):
            _add_signal(db, source="seek", captured=_iso(day + i * 0.001))
    for i in range(2):
        _add_signal(db, source="seek", captured=_iso(1 + i * 0.001))

    seek = _by_name(admin_api.source_health(user=ADMIN), "seek")
    assert seek["lastRunRecords"] == 2
    assert seek["totalRecords"] == 12
    assert seek["runDays"] == 3


def test_the_per_source_cap_quoted_is_the_one_the_pipeline_uses(db):
    """The page tells an admin a run sitting on this number was truncated. If
    the two numbers drift apart that claim becomes a lie."""
    from pipeline.live import DEFAULT_SCRAPE_LIMIT

    assert admin_api.source_health(user=ADMIN)["perSourceLimit"] == DEFAULT_SCRAPE_LIMIT


def test_unclassified_records_are_counted_separately(db):
    _add_signal(db, source="seek", captured=_iso(1), classified=True)
    _add_signal(db, source="seek", captured=_iso(1.1), classified=False)
    seek = _by_name(admin_api.source_health(user=ADMIN), "seek")
    assert seek["pending"] == 1
    assert seek["totalRecords"] == 2, "pending records are collected, just not read"


def test_a_retired_source_keeps_its_history_visible(db):
    """A renamed or removed scraper still has rows. Dropping them silently would
    make the all-time total disagree with the signals table."""
    _add_signal(db, source="oldboard", captured=_iso(30))
    payload = admin_api.source_health(user=ADMIN)
    old = _by_name(payload, "oldboard")
    assert old["status"] == "retired"
    assert payload["totalRecords"] == 1


# ---------- access endpoints ----------


def test_the_access_list_shows_every_door_at_once(db, monkeypatch):
    monkeypatch.setattr(
        "api.access.settings",
        type("S", (), {"allowed_google_domain": "easyskill.com",
                       "allowed_emails": ["legacy@gmail.com"]})(),
    )
    monkeypatch.setattr(
        admin_api, "settings",
        type("S", (), {"allowed_google_domain": "easyskill.com"})(),
    )
    access.upsert_user("named@gmail.com", "member", added_by="boss", target=db)

    payload = admin_api.list_access(user=ADMIN)
    assert [u["email"] for u in payload["users"]] == ["named@gmail.com"]
    assert [g["email"] for g in payload["envGrants"]] == ["legacy@gmail.com"]
    assert payload["domain"] == "easyskill.com", "the domain rule is stated too"


def test_revoking_says_so_when_another_door_is_still_open(db, monkeypatch):
    """The address is also in ALLOWED_EMAILS. Reporting a clean removal would
    let an admin believe somebody is locked out when they are not."""
    monkeypatch.setattr(
        "api.access.settings",
        type("S", (), {"allowed_google_domain": "", "allowed_emails": ["legacy@gmail.com"]})(),
    )
    monkeypatch.setattr(admin_api, "settings",
                        type("S", (), {"allowed_google_domain": ""})())
    access.upsert_user("legacy@gmail.com", "member", added_by="boss", target=db)

    payload = admin_api.revoke_access("legacy@gmail.com", user=ADMIN)
    assert payload["users"] == []
    assert "ALLOWED_EMAILS" in (payload["warning"] or "")


def test_revoking_a_domain_account_says_the_workspace_rule_still_admits_them(db, monkeypatch):
    monkeypatch.setattr(
        "api.access.settings",
        type("S", (), {"allowed_google_domain": "easyskill.com", "allowed_emails": []})(),
    )
    monkeypatch.setattr(admin_api, "settings",
                        type("S", (), {"allowed_google_domain": "easyskill.com"})())
    access.upsert_user("staff@easyskill.com", "admin", added_by="boss", target=db)
    access.upsert_user("other@easyskill.com", "admin", added_by="boss", target=db)

    payload = admin_api.revoke_access("staff@easyskill.com", user=ADMIN)
    assert "easyskill.com" in (payload["warning"] or "")


def test_revoking_a_clean_grant_warns_about_nothing(db, monkeypatch):
    monkeypatch.setattr(
        "api.access.settings",
        type("S", (), {"allowed_google_domain": "easyskill.com", "allowed_emails": []})(),
    )
    monkeypatch.setattr(admin_api, "settings",
                        type("S", (), {"allowed_google_domain": "easyskill.com"})())
    access.upsert_user("outsider@gmail.com", "member", added_by="boss", target=db)

    assert admin_api.revoke_access("outsider@gmail.com", user=ADMIN)["warning"] is None


def test_the_last_admin_guard_surfaces_as_a_refusal_not_a_crash(db, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        "api.access.settings",
        type("S", (), {"allowed_google_domain": "", "allowed_emails": []})(),
    )
    access.upsert_user("solo@easyskill.com", "admin", added_by="system", target=db)

    with pytest.raises(HTTPException) as exc:
        admin_api.revoke_access("solo@easyskill.com", user=ADMIN)
    assert exc.value.status_code == 400
    assert "only administrator" in exc.value.detail


def test_granting_a_bad_address_is_a_400_with_something_readable(db):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        admin_api.grant_access({"email": "nonsense", "role": "member"}, user=ADMIN)
    assert exc.value.status_code == 400
    assert "email address" in exc.value.detail


# ---------- the browser's half of the contract ----------
#
# Every write here reaches the API cross-origin (the web app on :3000, the API
# on :8787), so the browser sends a preflight first. A method missing from the
# CORS list fails that preflight and the request never arrives — which no test
# calling the endpoint directly can see, because none of them send a preflight.
# `PUT /api/admin/schedule` shipped broken exactly this way.


def test_every_method_the_client_uses_is_allowed_cross_origin():
    """Read the methods off the routes rather than listing them here, so a new
    endpoint with a new verb cannot be added without this noticing."""
    from starlette.middleware.cors import CORSMiddleware

    from api.server import app

    allowed = next(
        set(mw.kwargs["allow_methods"])
        for mw in app.user_middleware
        if mw.cls is CORSMiddleware
    )

    used = {
        method
        for route in app.routes
        for method in getattr(route, "methods", set())
        if method not in {"HEAD", "OPTIONS"}
    }
    assert used <= allowed, f"the browser cannot reach: {sorted(used - allowed)}"


# ---------- "Run now" and the run the schedule still owes ----------
#
# The window these cover is real and narrow: the server was down at 05:00, it
# comes back at 08:00, and an administrator presses Run now before the ticker's
# next pass. Both want to run the same week.


def _run_now_claim(db, monkeypatch, *, now, email="boss@easyskill.com"):
    """The claim half of POST /schedule/run, at a chosen moment."""
    import api.admin_api as mod
    from loader import run_log
    from loader.schedule import Schedule, due_occurrence

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(mod, "datetime", _Clock)
    monkeypatch.setattr(mod, "get_schedule", lambda *a, **k: Schedule())

    due = due_occurrence(Schedule(), now)
    if due is not None and run_log.has_run_for(due, db):
        due = None
    return run_log.claim(trigger=run_log.TRIGGER_MANUAL, due_at=due,
                         started_by=email, target=db)


def test_run_now_satisfies_a_scheduled_run_that_is_still_owed(db, monkeypatch):
    """Otherwise the ticker starts a second scrape a minute later: the same week
    collected twice, and the daily Gemini quota spent twice."""
    from loader import run_log
    from loader.schedule import Schedule, due_occurrence

    syd = ZoneInfo("Australia/Sydney")
    eight_am = datetime(2026, 9, 7, 8, 0, tzinfo=syd)          # Monday, 3h late
    due = due_occurrence(Schedule(), eight_am)
    assert due is not None, "the fixture moment must be inside the catch-up window"

    run_id = _run_now_claim(db, monkeypatch, now=eight_am)
    run_log.finish(run_id, status=run_log.STATUS_OK, collected=146, target=db)

    # What the ticker asks a minute later.
    assert run_log.has_run_for(due, db), "the ticker would start a second scrape"


def test_run_now_is_still_allowed_once_the_week_has_run(db, monkeypatch):
    """Satisfying the occurrence must not turn Run now into a once-a-week
    button — an administrator may deliberately collect twice."""
    from loader import run_log
    from loader.schedule import Schedule, due_occurrence

    syd = ZoneInfo("Australia/Sydney")
    eight_am = datetime(2026, 9, 7, 8, 0, tzinfo=syd)
    due = due_occurrence(Schedule(), eight_am)

    scheduled = run_log.claim(trigger=run_log.TRIGGER_SCHEDULE, due_at=due, target=db)
    run_log.finish(scheduled, status=run_log.STATUS_OK, collected=146, target=db)

    second = _run_now_claim(db, monkeypatch, now=eight_am)
    run_log.finish(second, status=run_log.STATUS_OK, collected=12, target=db)

    assert len(run_log.recent(5, db)) == 2


def test_run_now_outside_the_catch_up_window_owes_nothing(db, monkeypatch):
    """Pressed on a Thursday it is simply a manual run, and must not consume
    the coming Monday."""
    from loader import run_log
    from loader.schedule import Schedule, next_due

    syd = ZoneInfo("Australia/Sydney")
    thursday = datetime(2026, 9, 10, 14, 0, tzinfo=syd)

    run_id = _run_now_claim(db, monkeypatch, now=thursday)
    run_log.finish(run_id, status=run_log.STATUS_OK, target=db)

    coming_monday = next_due(Schedule(), thursday)
    assert not run_log.has_run_for(coming_monday, db), "it ate next week's run"


# ---------- a source that ships switched off ----------
#
# SEEK returns 403 to the deployed server's IP, so it defaults to off. The risk
# is not the default — it is somebody seeing an off toggle, assuming it was a
# mistake, switching it on, and collecting nothing for a week with no way to
# connect the two. So switching it on has to say what it is.


def test_seek_ships_switched_off_with_a_reason(db):
    payload = admin_api.source_health(ADMIN)
    seek = next(s for s in payload["sources"] if s["name"] == "seek")

    assert seek["enabled"] is False
    assert seek["defaultEnabled"] is False
    assert "403" in (seek["offReason"] or "")


def test_the_other_sources_are_unaffected(db):
    payload = admin_api.source_health(ADMIN)
    for s in payload["sources"]:
        if s["name"] in {"seek"} or s["status"] == "retired":
            continue
        assert s["defaultEnabled"] is True, s["name"]
        assert s["offReason"] is None, s["name"]


def test_switching_seek_on_returns_the_reason_as_a_warning(db):
    result = admin_api.set_source_enabled("seek", {"enabled": True}, ADMIN)

    assert result["warning"], "turning on a source that ships off said nothing"
    assert "403" in result["warning"]
    seek = next(s for s in result["sources"] if s["name"] == "seek")
    assert seek["enabled"] is True, "the request should still succeed"


def test_switching_seek_off_again_carries_no_warning(db):
    admin_api.set_source_enabled("seek", {"enabled": True}, ADMIN)
    result = admin_api.set_source_enabled("seek", {"enabled": False}, ADMIN)

    assert not result.get("warning")


def test_switching_on_a_normal_source_carries_no_warning(db):
    admin_api.set_source_enabled("adzuna", {"enabled": False}, ADMIN)
    result = admin_api.set_source_enabled("adzuna", {"enabled": True}, ADMIN)

    assert not result.get("warning"), "a routine toggle should not lecture the admin"


def test_seek_is_left_out_of_the_next_scrape_by_default(db):
    """The point of the default, stated where the pipeline reads it."""
    from loader.source_settings import enabled_sources

    assert "seek" not in enabled_sources(db)
