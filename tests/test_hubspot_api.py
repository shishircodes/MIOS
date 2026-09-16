"""The HubSpot admin endpoints, over HTTP.

Pinned: every route is administrators-only, the service key never comes back in
a response, and HubSpot's own failures reach the panel as messages rather than
server errors. HubSpot itself is replaced by a fake.
"""
from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from config.settings import settings as real_settings
from loader import credentials
from loader import hubspot_watchlist as hs
from loader.ingest import init_db

KEY = "pat-na1-0000-test-only-service-key-wxyz"

ROUTES = [
    ("get", "/api/admin/hubspot", None),
    ("put", "/api/admin/hubspot/key", {"key": KEY}),
    ("delete", "/api/admin/hubspot/key", None),
    ("get", "/api/admin/hubspot/properties", None),
    ("put", "/api/admin/hubspot/mapping", {"tierProperty": "t", "tierMap": {"a": "A"}}),
    ("post", "/api/admin/hubspot/sync", {"dryRun": True}),
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    path = tmp_path / "hubspot-api.db"
    init_db(path, watchlist_path=wl)
    monkeypatch.setattr("loader.db.settings",
                        dataclasses.replace(real_settings, db_path=path, database_url=None))
    # Whatever the developer's .env holds must not decide these tests.
    monkeypatch.delenv(hs.KEY_ENV, raising=False)
    monkeypatch.delenv(credentials.KEY_ENV, raising=False)
    credentials._fernet_for.cache_clear()
    return path


def _client(monkeypatch, *, auth_disabled: bool) -> TestClient:
    monkeypatch.setattr("api.auth.settings",
                        dataclasses.replace(real_settings, auth_disabled=auth_disabled))
    from api.server import app

    return TestClient(app)


@pytest.fixture
def admin(db, monkeypatch):
    return _client(monkeypatch, auth_disabled=True)


@pytest.fixture
def anon(db, monkeypatch):
    return _client(monkeypatch, auth_disabled=False)


@pytest.fixture
def member(db, monkeypatch):
    monkeypatch.setattr("api.auth.current_user",
                        lambda request: {"email": "member@easyskill.com", "name": "Member"})
    monkeypatch.setattr("api.access.is_admin", lambda email: False)
    return _client(monkeypatch, auth_disabled=False)


def _send(client, method, path, body):
    if body is None:
        return getattr(client, method)(path)
    return client.request(method.upper(), path, json=body)


class FakeHubSpot:
    def __init__(self, key, **_kw):
        if not key:
            raise hs.HubSpotNotConfigured("No HubSpot service key.")

    def company_properties(self):
        return [
            {"name": "hs_ideal_customer_profile", "label": "Ideal Customer Profile Tier",
             "type": "enumeration",
             "options": [{"value": "tier_1", "label": "Tier 1"}, {"value": "tier_2", "label": "Tier 2"}]},
            {"name": "name", "label": "Company name", "type": "string", "options": []},
        ]

    def search_companies(self, properties, filters):
        return [{"id": "7", "properties": {"name": "BHP Group", "hs_ideal_customer_profile": "tier_2",
                                           "hs_is_target_account": "true"}}], False


class RefusingHubSpot(FakeHubSpot):
    def company_properties(self):
        raise hs.HubSpotError("HubSpot refused the service key.", 401)

    search_companies = company_properties


# ---------- who may call them ----------


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_signed_out_callers_are_refused(anon, method, path, body):
    assert _send(anon, method, path, body).status_code == 401


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_members_are_refused(member, method, path, body):
    assert _send(member, method, path, body).status_code == 403


# ---------- the key ----------


def test_a_key_cannot_be_stored_without_the_encryption_secret(admin):
    response = admin.put("/api/admin/hubspot/key", json={"key": KEY})
    assert response.status_code == 503
    assert credentials.KEY_ENV in response.json()["detail"]


def test_a_stored_key_is_never_returned(admin, monkeypatch):
    monkeypatch.setenv(credentials.KEY_ENV, "a-stable-bootstrap-secret")

    saved = admin.put("/api/admin/hubspot/key", json={"key": KEY})
    status = admin.get("/api/admin/hubspot")

    assert saved.status_code == 200 and status.status_code == 200
    for body in (saved.text, status.text):
        assert KEY not in body and KEY[:16] not in body
    assert status.json()["key"] == {"source": "panel", "hint": KEY[-4:],
                                    "shadowsEnvironment": False, "unreadable": False}


def test_an_empty_key_is_refused(admin):
    assert admin.put("/api/admin/hubspot/key", json={"key": "  "}).status_code == 400


# ---------- fields, mapping, sync ----------


def test_without_a_key_loading_fields_says_so(admin):
    response = admin.get("/api/admin/hubspot/properties")
    assert response.status_code == 400
    assert "service key" in response.json()["detail"]


def test_fields_offer_only_those_with_options_as_tier_candidates(admin, monkeypatch):
    monkeypatch.setenv(hs.KEY_ENV, KEY)
    monkeypatch.setattr(hs, "HubSpotClient", FakeHubSpot)

    body = admin.get("/api/admin/hubspot/properties").json()

    assert [p["name"] for p in body["tierCandidates"]] == ["hs_ideal_customer_profile"]
    assert len(body["properties"]) == 2


def test_hubspot_refusing_the_key_is_a_message_not_a_crash(admin, monkeypatch):
    monkeypatch.setenv(hs.KEY_ENV, KEY)
    monkeypatch.setattr(hs, "HubSpotClient", RefusingHubSpot)

    response = admin.get("/api/admin/hubspot/properties")

    assert response.status_code == 502
    assert "refused" in response.json()["detail"]


def test_an_invalid_mapping_is_refused(admin):
    response = admin.put("/api/admin/hubspot/mapping", json={"tierProperty": "", "tierMap": {}})
    assert response.status_code == 400


def test_a_saved_mapping_comes_back(admin):
    response = admin.put("/api/admin/hubspot/mapping", json={
        "tierProperty": "client_tier", "tierMap": {"gold": "A", "silver": "B", "bronze": ""},
        "industryProperty": "industry", "targetAccountsOnly": False, "autoSync": False,
    })
    mapping = response.json()["mapping"]
    assert response.status_code == 200
    assert mapping["tierProperty"] == "client_tier"
    assert mapping["tierMap"] == {"gold": "A", "silver": "B"}
    assert mapping["targetAccountsOnly"] is False and mapping["autoSync"] is False


def test_a_preview_returns_the_plan_and_changes_nothing(admin, monkeypatch):
    monkeypatch.setenv(hs.KEY_ENV, KEY)
    monkeypatch.setattr(hs, "HubSpotClient", FakeHubSpot)

    body = admin.post("/api/admin/hubspot/sync", json={"dryRun": True}).json()

    assert body["result"]["dryRun"] is True
    assert body["result"]["preview"]["matched"] == [{"hubspotName": "BHP Group", "company_name": "BHP"}]
    assert body["lastSync"] is None
    assert KEY not in json.dumps(body)


def test_applying_a_sync_records_it(admin, monkeypatch):
    monkeypatch.setenv(hs.KEY_ENV, KEY)
    monkeypatch.setattr(hs, "HubSpotClient", FakeHubSpot)

    body = admin.post("/api/admin/hubspot/sync", json={"dryRun": False}).json()

    assert body["result"]["updated"] == 1
    assert body["lastSync"]["total"] == 1
    assert body["watchlist"] == {"fromHubspot": 1, "fromSeed": 0}
