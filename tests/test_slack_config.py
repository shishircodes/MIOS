"""The Slack digest, configured from Admin › Integrations.

Pinned: the webhook is stored encrypted and never returned, the environment
variable still works, switching the post off skips it without forgetting the
webhook, every delivery's outcome is recorded with Slack's own answer, and only
administrators can change any of it. Slack itself is replaced by a fake.
"""
from __future__ import annotations

import dataclasses
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from config.settings import settings as real_settings
from delivery import slack_config
from loader import credentials
from loader.db import connect
from loader.ingest import init_db

HOOK = "https://hooks.slack.com/services/T000/B000/test-only-secret-abcd"
ENV_HOOK = "https://hooks.slack.com/services/T111/B111/env-secret-wxyz"


@pytest.fixture
def db(tmp_path, monkeypatch):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    path = tmp_path / "slack.db"
    init_db(path, watchlist_path=wl)
    patched = dataclasses.replace(real_settings, db_path=path, database_url=None,
                                  slack_webhook_url="")
    monkeypatch.setattr("loader.db.settings", patched)
    monkeypatch.setattr("config.settings.settings", patched)
    monkeypatch.setenv(credentials.KEY_ENV, "test-only-bootstrap-passphrase")
    credentials._fernet_for.cache_clear()
    return path


def _slack(status=200, text="ok"):
    return patch("delivery.slack.requests.post",
                 return_value=MagicMock(status_code=status, text=text))


def test_a_stored_webhook_is_encrypted_and_never_returned(db):
    slack_config.set_webhook(HOOK, changed_by="admin@easyskill.com")
    assert slack_config.webhook() == HOOK
    st = slack_config.status()
    assert HOOK not in json.dumps(st) and "test-only-secret" not in json.dumps(st)
    assert st["webhook"] == {"source": "panel", "hint": "abcd", "shadowsEnvironment": False,
                             "unreadable": False}
    with connect(db, readonly=True) as conn:
        rows = json.dumps([dict(r) for r in conn.execute("SELECT * FROM llm_credentials")])
    assert "test-only-secret" not in rows


def test_only_incoming_webhooks_are_accepted(db):
    for bad in ("https://example.com/hook", "https://hooks.slack.com/triggers/T/1/abc", ""):
        with pytest.raises(slack_config.SlackConfigError, match="incoming-webhook"):
            slack_config.set_webhook(bad, changed_by="a")


def test_the_environment_still_works_and_the_panel_wins(db):
    assert slack_config.webhook(env_value=ENV_HOOK) == ENV_HOOK
    slack_config.set_webhook(HOOK, changed_by="a")
    assert slack_config.webhook(env_value=ENV_HOOK) == HOOK
    slack_config.clear_webhook()
    assert slack_config.webhook(env_value=ENV_HOOK) == ENV_HOOK


def test_the_env_example_placeholder_counts_as_unset(db):
    assert slack_config.webhook(env_value="https://hooks.slack.com/services/...") == ""


def test_a_run_posts_the_digest_and_records_it(db):
    slack_config.set_webhook(HOOK, changed_by="a")
    with _slack() as post:
        assert slack_config.deliver_digest("*digest*") is True
    assert post.call_args.kwargs["json"]["text"] == "*digest*"
    last = slack_config.last_delivery()
    assert last["kind"] == "digest" and last["ok"] is True


def test_slacks_own_answer_is_kept_when_it_fails(db):
    slack_config.set_webhook(HOOK, changed_by="a")
    with _slack(404, "no_service"):
        assert slack_config.deliver_digest("x") is False
    assert slack_config.last_delivery()["detail"] == "Slack answered 404: no_service"


def test_switched_off_skips_the_post_but_keeps_the_webhook(db):
    slack_config.set_webhook(HOOK, changed_by="a")
    slack_config.set_enabled(False, changed_by="admin@easyskill.com")
    with _slack() as post:
        assert slack_config.deliver_digest("x") is False
    post.assert_not_called()
    assert slack_config.webhook() == HOOK
    assert "switched off" in slack_config.last_delivery()["detail"]
    assert slack_config.status()["changedBy"] == "admin@easyskill.com"


def test_no_webhook_means_no_post(db):
    with _slack() as post:
        assert slack_config.deliver_digest("x") is False
    post.assert_not_called()


def test_the_pipeline_posts_through_the_panel_webhook(db, monkeypatch):
    """The run reads the webhook set in the panel, not only SLACK_WEBHOOK_URL."""
    from pipeline import live

    slack_config.set_webhook(HOOK, changed_by="a")
    monkeypatch.setattr("pipeline.live.settings",
                        type("S", (), {"db_path": db, "slack_webhook_url": ""})())
    with _slack() as post:
        summary = live.run_live_cycle(db_path=db, do_scrape=False, do_pulse=False,
                                      do_slack=True, gemini_caller=lambda *a, **k: [])
    assert summary["slack_ok"] is True
    assert post.call_args.args[0] == HOOK


# ---------- over HTTP ----------


def _client(monkeypatch, *, auth_disabled: bool) -> TestClient:
    monkeypatch.setattr("api.auth.settings",
                        dataclasses.replace(real_settings, auth_disabled=auth_disabled))
    from api.server import app

    return TestClient(app)


@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/slack"), ("put", "/api/admin/slack/webhook"),
    ("delete", "/api/admin/slack/webhook"), ("put", "/api/admin/slack/settings"),
    ("post", "/api/admin/slack/test"),
])
def test_every_route_is_for_administrators(db, monkeypatch, method, path):
    anon = _client(monkeypatch, auth_disabled=False)
    kw = {"json": {"url": HOOK, "enabled": False}} if method == "put" else {}
    assert getattr(anon, method)(path, **kw).status_code == 401
    monkeypatch.setattr("api.auth.current_user",
                        lambda request: {"email": "member@easyskill.com", "name": "Member"})
    assert getattr(anon, method)(path, **kw).status_code == 403


def test_the_panel_flow(db, monkeypatch):
    admin = _client(monkeypatch, auth_disabled=True)
    assert admin.post("/api/admin/slack/test").status_code == 400, "nothing to test yet"

    r = admin.put("/api/admin/slack/webhook", json={"url": HOOK})
    assert r.status_code == 200 and HOOK not in r.text

    with _slack():
        r = admin.post("/api/admin/slack/test")
    assert r.status_code == 200 and r.json()["testOk"] is True
    assert r.json()["lastDelivery"]["kind"] == "test"

    r = admin.put("/api/admin/slack/settings", json={"enabled": False})
    assert r.json()["enabled"] is False
    assert admin.put("/api/admin/slack/settings", json={"enabled": "no"}).status_code == 400
    assert admin.put("/api/admin/slack/webhook", json={"url": "https://x.com"}).status_code == 400
