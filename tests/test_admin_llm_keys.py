"""The admin endpoints for entering provider API keys.

The one property worth more than the rest: **the key never comes back out.**
Every endpoint here returns the full LLM settings payload, which is the same
object the panel renders, so a key leaking into it would be displayed in a
browser and cached wherever that response is cached.

Note the fixture patches `loader.credentials.connect` as well as the endpoints'.
It holds its own reference and resolves its own target, exactly like
`loader.source_settings` — which is how an earlier test run reached the
production database and left a row in it.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from api import admin_api
from loader import credentials
from loader.credentials import KEY_ENV
from loader.db import connect
from loader.ingest import init_db

ADMIN = {"email": "boss@easyskill.com", "role": "admin"}
SECRET = "a-stable-bootstrap-secret"
KEY = "AIzaSyExampleKeyForTestsOnly-1234wxyz"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY_ENV, SECRET)
    credentials._fernet_for.cache_clear()

    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    path = tmp_path / "keys.db"
    init_db(path, watchlist_path=wl)

    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setattr("api.admin_api.connect", lambda t=None, **kw: connect(path, **kw))
    # Its own reference, its own target — see the module docstring.
    monkeypatch.setattr("loader.credentials.connect",
                        lambda t=None, **kw: connect(path, **kw))
    monkeypatch.setattr("loader.llm_settings.connect",
                        lambda t=None, **kw: connect(path, **kw))
    monkeypatch.setattr("llm.usage.connect", lambda t=None, **kw: connect(path, **kw))
    return path


# ---------- the key does not come back ----------


def test_the_response_never_contains_the_key(db):
    result = admin_api.set_provider_key("gemini", {"key": KEY}, user=ADMIN)

    assert KEY not in json.dumps(result)
    assert KEY[:12] not in json.dumps(result)


def test_the_panel_gets_a_hint_and_who_set_it(db):
    admin_api.set_provider_key("gemini", {"key": KEY}, user=ADMIN)

    result = admin_api.llm_settings(user=ADMIN)
    gemini = next(p for p in result["providers"] if p["name"] == "gemini")

    assert gemini["key"]["hint"] == KEY[-4:]
    assert gemini["key"]["source"] == "panel"
    assert gemini["key"]["changedBy"] == ADMIN["email"]


def test_a_stored_key_makes_the_provider_configured(db):
    """The whole point: usable without a redeploy."""
    before = admin_api.llm_settings(user=ADMIN)
    assert not next(p for p in before["providers"] if p["name"] == "anthropic")["configured"]

    after = admin_api.set_provider_key("anthropic", {"key": "sk-ant-example"}, user=ADMIN)

    assert next(p for p in after["providers"] if p["name"] == "anthropic")["configured"]


# ---------- refusals say which kind of problem it is ----------


def test_an_unknown_provider_is_refused_by_name(db):
    with pytest.raises(HTTPException) as exc:
        admin_api.set_provider_key("openai", {"key": KEY}, user=ADMIN)

    assert exc.value.status_code == 400
    assert "openai" in str(exc.value.detail)


def test_an_empty_key_is_refused(db):
    with pytest.raises(HTTPException) as exc:
        admin_api.set_provider_key("gemini", {"key": "  "}, user=ADMIN)

    assert exc.value.status_code == 400


def test_a_deployment_that_cannot_encrypt_says_so_and_stores_nothing(db, monkeypatch):
    """503 rather than 400: the request was fine, the deployment is not set up
    to accept it, and the message has to name the variable to set."""
    monkeypatch.delenv(KEY_ENV, raising=False)
    credentials._fernet_for.cache_clear()

    with pytest.raises(HTTPException) as exc:
        admin_api.set_provider_key("gemini", {"key": KEY}, user=ADMIN)

    assert exc.value.status_code == 503
    assert KEY_ENV in str(exc.value.detail)
    with connect(db, readonly=True) as conn:
        assert conn.execute("SELECT count(*) FROM llm_credentials").fetchone()[0] == 0


# ---------- clearing ----------


def test_clearing_removes_the_key(db):
    admin_api.set_provider_key("gemini", {"key": KEY}, user=ADMIN)

    result = admin_api.clear_provider_key("gemini", user=ADMIN)

    gemini = next(p for p in result["providers"] if p["name"] == "gemini")
    assert gemini["key"]["source"] != "panel"


def test_clearing_a_provider_with_no_key_is_not_an_error(db):
    """Idempotent: a second click, or two admins at once, must not 500."""
    result = admin_api.clear_provider_key("anthropic", user=ADMIN)

    assert "note" in result


# ---------- the test button ----------


def test_testing_a_key_reports_failure_rather_than_raising(db, monkeypatch):
    """Every outcome is something the panel displays. The conftest guard blocks
    real model calls, so this exercises the unreachable-provider path."""
    admin_api.set_provider_key("gemini", {"key": KEY}, user=ADMIN)

    result = admin_api.test_provider_key("gemini", {}, user=ADMIN)

    assert result["test"]["ok"] is False
    assert result["test"]["provider"] == "gemini"
    assert result["test"]["message"]


def test_testing_an_unknown_provider_is_refused(db):
    with pytest.raises(HTTPException) as exc:
        admin_api.test_provider_key("openai", {}, user=ADMIN)

    assert exc.value.status_code == 400


def test_a_rejected_call_is_still_counted(db, monkeypatch):
    """The provider charges for a rejected request exactly as for a served one.
    A test button that counted only successes would let somebody burn a free
    tier by pressing it.

    The caller has to be built for this: a provider that cannot be built made no
    call at all, and counting *that* would inflate the number in the opposite
    direction — see the test below.
    """
    from llm import providers, usage

    def _rejects(model):
        def _call(*_a, **_k):
            raise RuntimeError("API key not valid")
        return _call

    monkeypatch.setattr(providers._PROVIDERS["gemini"], "build", _rejects)

    before = usage.used_today("gemini", db)
    result = admin_api.test_provider_key("gemini", {}, user=ADMIN)

    assert result["test"]["ok"] is False
    assert usage.used_today("gemini", db) == before + 1


def test_an_unbuildable_provider_is_not_counted(db, monkeypatch):
    """No key means no request was made, so nothing was spent. Counting it
    would report allowance consumed by a button that never reached the network.
    """
    from llm import providers, usage

    def _unconfigured(model):
        raise providers.ProviderNotConfigured("no key")

    monkeypatch.setattr(providers._PROVIDERS["anthropic"], "build", _unconfigured)

    before = usage.used_today("anthropic", db)
    result = admin_api.test_provider_key("anthropic", {}, user=ADMIN)

    assert result["test"]["ok"] is False
    assert usage.used_today("anthropic", db) == before
