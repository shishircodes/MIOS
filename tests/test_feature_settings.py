"""Switching Mode Push's AI notes off.

The notes never affect a score or the order, which is what makes them safe to
leave to an administrator. What is pinned here: the switch is remembered with
who changed it, an untouched database behaves exactly as before (notes on), and
a switched-off search never calls a model.
"""
from __future__ import annotations

import json

import pytest

from api import admin_api, push_api
from loader import feature_settings
from loader.db import connect
from loader.feature_settings import PUSH_RATIONALE, UnknownFeature
from loader.ingest import init_db

ADMIN = {"email": "boss@easyskill.com", "role": "admin"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    path = tmp_path / "features.db"
    init_db(path, watchlist_path=wl)
    # Each module holds its own `connect`, so each is pointed at this file.
    for mod in ("loader.feature_settings", "api.admin_api", "loader.llm_settings",
                "loader.credentials", "llm.usage"):
        monkeypatch.setattr(f"{mod}.connect", lambda t=None, **kw: connect(path, **kw))
    return path


def test_an_untouched_database_keeps_the_notes_on(db):
    assert feature_settings.is_enabled(PUSH_RATIONALE) is True
    state = feature_settings.describe(PUSH_RATIONALE)
    assert state["changedBy"] is None and state["default"] is True


def test_switching_off_is_remembered_with_who_did_it(db):
    result = admin_api.set_push_rationale({"enabled": False}, user=ADMIN)

    assert result["pushRationale"]["enabled"] is False
    assert result["pushRationale"]["changedBy"] == ADMIN["email"]
    assert feature_settings.is_enabled(PUSH_RATIONALE) is False


def test_switching_back_on_returns_to_the_default_rather_than_storing_it(db):
    admin_api.set_push_rationale({"enabled": False}, user=ADMIN)
    admin_api.set_push_rationale({"enabled": True}, user=ADMIN)

    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM feature_settings").fetchone()[0] == 0


def test_the_request_must_say_on_or_off(db):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as err:
        admin_api.set_push_rationale({}, user=ADMIN)
    assert err.value.status_code == 400


def test_an_unknown_feature_is_refused(db):
    with pytest.raises(UnknownFeature):
        feature_settings.set_enabled("time_travel", True, changed_by=ADMIN["email"])


def test_a_database_without_the_table_still_records_a_change(tmp_path, monkeypatch):
    """A database initialised before this feature existed has no table."""
    path = tmp_path / "old.db"
    with connect(path) as conn:
        conn.execute("CREATE TABLE placeholder (id INTEGER)")
    monkeypatch.setattr("loader.feature_settings.connect",
                        lambda t=None, **kw: connect(path, **kw))

    assert feature_settings.is_enabled(PUSH_RATIONALE) is True, "fails open to the default"
    feature_settings.set_enabled(PUSH_RATIONALE, False, changed_by=ADMIN["email"])
    assert feature_settings.is_enabled(PUSH_RATIONALE) is False


def test_a_switched_off_search_never_calls_the_model(monkeypatch):
    calls = []
    monkeypatch.setattr(push_api, "signals_for_matching", lambda days: [])
    monkeypatch.setattr(push_api, "is_enabled", lambda name: False)
    monkeypatch.setattr(push_api, "annotate", lambda *a, **k: calls.append(1) or (a[1], None))

    payload = push_api._matches_for({"currentTitle": "Planner"}, days=30, limit=5)

    assert calls == []
    assert payload["rationaleEnabled"] is False
    assert payload["rationaleNote"] is None, "off is a choice, not a failure to explain"


def test_a_switched_on_search_still_annotates(monkeypatch):
    calls = []
    monkeypatch.setattr(push_api, "signals_for_matching", lambda days: [])
    monkeypatch.setattr(push_api, "is_enabled", lambda name: True)
    monkeypatch.setattr(push_api, "annotate", lambda *a, **k: calls.append(1) or (a[1], None))

    payload = push_api._matches_for({"currentTitle": "Planner"}, days=30, limit=5)

    assert calls == [1]
    assert payload["rationaleEnabled"] is True
