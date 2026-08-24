"""Tests for the admin endpoints (api.admin_api).

Source health is derived from the signals rather than a run log, so what needs
pinning is the derivation: a run that stopped at the cap, a source that has gone
quiet, one that cannot run at all.
"""
from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta, timezone

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
    monkeypatch.setattr("api.admin_api.connect", lambda t=None: connect(path))
    monkeypatch.setattr("api.access.connect", lambda t=None: connect(path))
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
