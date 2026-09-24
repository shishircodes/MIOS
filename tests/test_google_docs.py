"""Sending quarterly reports to Google Docs.

Pinned: which OAuth client is used and in what order, that connecting cannot be
replayed or forged, that neither the client secret nor the refresh token is
stored readable or returned, and that a report keeps one Doc across exports —
falling back to a new Doc only when the old one is gone. Google is replaced by
a fake that records every request.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from config.settings import settings as real_settings
from loader import credentials
from loader.db import connect
from loader.ingest import init_db
from publish import google_docs as gd

CLIENT_ID = "123456789012-abcdefghijklmnop.apps.googleusercontent.com"
SECRET = "GOCSPX-test-only-client-secret"
REFRESH = "1//test-only-refresh-token"


class Resp:
    def __init__(self, status: int = 200, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class FakeGoogle:
    """Answers the handful of Google endpoints MIOS calls, and records them."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.files: dict[str, dict] = {}
        self.token_error: str | None = None
        self.granted = gd.SCOPES
        self.patch_status = 200
        self._n = 0

    def _id(self) -> str:
        self._n += 1
        return f"file{self._n}"

    def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        if url == gd.TOKEN_URL:
            if self.token_error:
                return Resp(400, {"error": self.token_error})
            if kw["data"]["grant_type"] == "authorization_code":
                return Resp(200, {"access_token": "at", "refresh_token": REFRESH, "scope": self.granted})
            return Resp(200, {"access_token": "at-refreshed"})
        if url == gd.REVOKE_URL:
            return Resp(200)
        if url == gd.FILES_URL:  # folder
            fid = self._id()
            self.files[fid] = {"mime": kw["json"]["mimeType"], "name": kw["json"]["name"]}
            return Resp(200, {"id": fid, "webViewLink": f"https://drive.google.com/drive/folders/{fid}"})
        if url == gd.UPLOAD_URL:
            meta = json.loads(kw["data"].split(b"\r\n\r\n", 1)[1].split(b"\r\n", 1)[0])
            fid = self._id()
            self.files[fid] = {"mime": meta["mimeType"], "name": meta["name"], "parents": meta["parents"],
                               "body": kw["data"]}
            return Resp(200, {"id": fid, "webViewLink": f"https://docs.google.com/document/d/{fid}/edit"})
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        if url == gd.USERINFO_URL:
            return Resp(200, {"email": "reports@easyskill.com.au"})
        if url.startswith(gd.FILES_URL + "/"):
            fid = url.rsplit("/", 1)[1]
            return Resp(200, {"id": fid}) if fid in self.files else Resp(404)
        raise AssertionError(f"unexpected GET {url}")

    def patch(self, url, **kw):
        self.calls.append(("patch", url, kw))
        fid = url.rsplit("/", 1)[1]
        if self.patch_status != 200:
            return Resp(self.patch_status, {"error": {"message": "File not found"}})
        self.files[fid]["body"] = kw["data"]
        return Resp(200, {"id": fid, "webViewLink": f"https://docs.google.com/document/d/{fid}/edit"})

    def count(self, method: str, url: str) -> int:
        return sum(1 for m, u, _ in self.calls if m == method and u.startswith(url))


@pytest.fixture
def db(tmp_path, monkeypatch):
    wl = tmp_path / "wl.json"
    wl.write_text("[]")
    path = tmp_path / "gdocs.db"
    init_db(path, watchlist_path=wl)
    patched = dataclasses.replace(real_settings, db_path=path, database_url=None,
                                  google_client_id="", google_client_secret="",
                                  oauth_redirect_uri="https://mios.example.com/auth/callback",
                                  web_app_url="https://app.example.com")
    monkeypatch.setattr("loader.db.settings", patched)
    monkeypatch.setattr("config.settings.settings", patched)
    for env in (gd.CLIENT_ID_ENV, gd.CLIENT_SECRET_ENV, gd.REDIRECT_ENV):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv(credentials.KEY_ENV, "test-only-bootstrap-passphrase")
    credentials._fernet_for.cache_clear()
    return path


@pytest.fixture
def google():
    return FakeGoogle()


def _connect(google) -> None:
    gd.set_client(CLIENT_ID, SECRET, changed_by="admin@easyskill.com")
    url = gd.start_connect("admin@easyskill.com")
    state = parse_qs(urlsplit(url).query)["state"][0]
    gd.finish_connect("the-code", state, http=google)


def _report(status="draft", rid="rep-1"):
    return {"id": rid, "title": "Quarterly Market Report 2026-Q3", "quarter": "2026-Q3",
            "status": status, "signalsAnalysed": 12, "approvedBy": None, "approvedAt": None,
            "sections": [{"heading": "Mining", "body": "Intro.\n\n| A | B |\n|---|---|\n| x | 1 |"}]}


# ---------- which client ----------


def test_with_nothing_configured_there_is_no_client(db):
    assert gd.client()["source"] == "none"
    with pytest.raises(gd.GoogleDocsNotConfigured):
        gd.start_connect("admin@easyskill.com")


def test_the_sign_in_client_is_reused_when_nothing_else_is_set(db, monkeypatch):
    monkeypatch.setattr("config.settings.settings", dataclasses.replace(
        real_settings, google_client_id="sign-in-id", google_client_secret="sign-in-secret"))
    assert gd.client() == {"id": "sign-in-id", "secret": "sign-in-secret", "source": "sign-in"}


def test_the_environment_beats_the_sign_in_client(db, monkeypatch):
    monkeypatch.setattr("config.settings.settings", dataclasses.replace(
        real_settings, google_client_id="sign-in-id", google_client_secret="sign-in-secret"))
    monkeypatch.setenv(gd.CLIENT_ID_ENV, "env-id")
    monkeypatch.setenv(gd.CLIENT_SECRET_ENV, "env-secret")
    assert gd.client()["source"] == "environment"


def test_a_client_entered_in_the_panel_wins_and_its_secret_is_encrypted(db, monkeypatch):
    monkeypatch.setenv(gd.CLIENT_ID_ENV, "env-id")
    monkeypatch.setenv(gd.CLIENT_SECRET_ENV, "env-secret")
    gd.set_client(CLIENT_ID, SECRET, changed_by="admin@easyskill.com")
    assert gd.client() == {"id": CLIENT_ID, "secret": SECRET, "source": "panel"}
    with connect(db, readonly=True) as conn:
        dump = json.dumps([dict(r) for r in conn.execute("SELECT * FROM llm_credentials").fetchall()])
    assert SECRET not in dump


def test_removing_the_panel_client_falls_back_to_the_environment(db, monkeypatch):
    monkeypatch.setenv(gd.CLIENT_ID_ENV, "env-id")
    monkeypatch.setenv(gd.CLIENT_SECRET_ENV, "env-secret")
    gd.set_client(CLIENT_ID, SECRET, changed_by="admin@easyskill.com")
    assert gd.clear_client()
    assert gd.client()["source"] == "environment"


def test_the_redirect_uri_sits_beside_the_sign_in_callback(db, monkeypatch):
    assert gd.redirect_uri() == "https://mios.example.com/api/admin/google-docs/callback"
    monkeypatch.setenv(gd.REDIRECT_ENV, "https://elsewhere.example.com/cb")
    assert gd.redirect_uri() == "https://elsewhere.example.com/cb"


# ---------- connecting ----------


def test_the_consent_url_asks_for_offline_drive_file_access(db):
    gd.set_client(CLIENT_ID, SECRET, changed_by="admin@easyskill.com")
    q = parse_qs(urlsplit(gd.start_connect("admin@easyskill.com")).query)
    assert q["client_id"] == [CLIENT_ID]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert "https://www.googleapis.com/auth/drive.file" in q["scope"][0].split()
    assert "https://www.googleapis.com/auth/drive" not in q["scope"][0].split(), "never the whole Drive"
    assert q["redirect_uri"] == [gd.redirect_uri()]


def test_connecting_stores_the_token_encrypted_and_names_the_account(db, google):
    _connect(google)
    st = gd.status()
    assert st["connected"] is True
    assert st["account"]["email"] == "reports@easyskill.com.au"
    assert st["account"]["connectedBy"] == "admin@easyskill.com"
    with connect(db, readonly=True) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM llm_credentials").fetchall()]
        kv = [dict(r) for r in conn.execute("SELECT * FROM kv_store").fetchall()]
    assert REFRESH not in json.dumps(rows) + json.dumps(kv)
    assert REFRESH not in json.dumps(st) and SECRET not in json.dumps(st)


def test_a_state_that_mios_did_not_issue_is_refused(db, google):
    gd.set_client(CLIENT_ID, SECRET, changed_by="admin@easyskill.com")
    with pytest.raises(gd.GoogleDocsError, match="not one MIOS issued"):
        gd.finish_connect("code", "forged-state", http=google)
    assert google.calls == [], "nothing was sent to Google"


def test_a_state_works_once(db, google):
    gd.set_client(CLIENT_ID, SECRET, changed_by="admin@easyskill.com")
    state = parse_qs(urlsplit(gd.start_connect("admin@easyskill.com")).query)["state"][0]
    gd.finish_connect("code", state, http=google)
    with pytest.raises(gd.GoogleDocsError, match="already used"):
        gd.finish_connect("code", state, http=google)


def test_an_expired_state_is_refused(db, google):
    gd.set_client(CLIENT_ID, SECRET, changed_by="admin@easyskill.com")
    state = parse_qs(urlsplit(gd.start_connect("admin@easyskill.com")).query)["state"][0]
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    gd._kv_set(gd.STATE_PREFIX + state, {"by": "admin@easyskill.com", "expires": past}, None)
    with pytest.raises(gd.GoogleDocsError, match="expired"):
        gd.finish_connect("code", state, http=google)


def test_unticking_drive_on_the_consent_screen_is_explained(db, google):
    google.granted = "openid email"
    gd.set_client(CLIENT_ID, SECRET, changed_by="admin@easyskill.com")
    state = parse_qs(urlsplit(gd.start_connect("admin@easyskill.com")).query)["state"][0]
    with pytest.raises(gd.GoogleDocsError, match="Drive access was not granted"):
        gd.finish_connect("code", state, http=google)
    assert gd.status()["connected"] is False


def test_disconnecting_revokes_at_google_and_forgets(db, google):
    _connect(google)
    assert gd.disconnect(http=google)
    assert google.count("post", gd.REVOKE_URL) == 1
    st = gd.status()
    assert st["connected"] is False and st["account"] is None and st["folder"] is None


# ---------- exporting ----------


def test_export_without_a_connection_says_where_to_connect(db, google):
    with pytest.raises(gd.GoogleDocsNotConfigured, match="Integrations"):
        gd.export(_report(), "<p>x</p>", by="a@easyskill.com", http=google)


def test_the_first_export_makes_a_folder_and_a_google_doc(db, google):
    _connect(google)
    out = gd.export(_report(), "<h1>Report</h1>", by="a@easyskill.com", http=google)
    assert out["created"] is True
    doc = google.files[out["fileId"]]
    assert doc["mime"] == gd.DOC_MIME, "converted to a Google Doc, not stored as an HTML file"
    assert doc["name"] == "Quarterly Market Report 2026-Q3 (DRAFT)"
    folder = google.files[doc["parents"][0]]
    assert folder["mime"] == gd.FOLDER_MIME and folder["name"] == gd.FOLDER_NAME
    assert gd.export_record("rep-1")["url"] == out["url"]


def test_exporting_again_updates_the_same_doc(db, google):
    _connect(google)
    first = gd.export(_report(), "<p>one</p>", by="a@easyskill.com", http=google)
    second = gd.export(_report(status="approved"), "<p>two</p>", by="b@easyskill.com", http=google)
    assert second["fileId"] == first["fileId"] and second["url"] == first["url"]
    assert second["created"] is False
    assert google.count("post", gd.UPLOAD_URL) == 1, "no second Doc"
    assert b"<p>two</p>" in google.files[first["fileId"]]["body"]
    assert gd.export_record("rep-1")["exportedBy"] == "b@easyskill.com"


def test_reports_share_one_folder(db, google):
    _connect(google)
    a = gd.export(_report(rid="r1"), "<p>a</p>", by="x", http=google)
    b = gd.export(_report(rid="r2"), "<p>b</p>", by="x", http=google)
    assert google.files[a["fileId"]]["parents"] == google.files[b["fileId"]]["parents"]
    assert google.count("post", gd.FILES_URL) == 1, "the folder is made once"


def test_a_doc_deleted_in_drive_is_replaced_and_the_new_link_flagged(db, google):
    _connect(google)
    first = gd.export(_report(), "<p>one</p>", by="x", http=google)
    google.patch_status = 404
    second = gd.export(_report(), "<p>two</p>", by="x", http=google)
    assert second["created"] is True and second["replacedLink"] is True
    assert second["fileId"] != first["fileId"]
    assert gd.export_record("rep-1")["fileId"] == second["fileId"]


def test_revoked_access_asks_for_a_reconnect(db, google):
    _connect(google)
    google.token_error = "invalid_grant"
    with pytest.raises(gd.GoogleDocsNotConfigured, match="Reconnect"):
        gd.export(_report(), "<p>x</p>", by="x", http=google)


def test_an_oversized_report_is_refused_before_calling_google(db, google):
    _connect(google)
    before = len(google.calls)
    with pytest.raises(gd.GoogleDocsError, match="5 MB"):
        gd.export(_report(), "x" * (5 * 1024 * 1024 + 1), by="x", http=google)
    assert len(google.calls) == before


# ---------- over HTTP ----------


def _client(monkeypatch, *, auth_disabled: bool) -> TestClient:
    monkeypatch.setattr("api.auth.settings",
                        dataclasses.replace(real_settings, auth_disabled=auth_disabled))
    from api.server import app

    return TestClient(app)


@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/google-docs"),
    ("put", "/api/admin/google-docs/client"),
    ("delete", "/api/admin/google-docs/client"),
    ("post", "/api/admin/google-docs/connect"),
    ("delete", "/api/admin/google-docs/connection"),
])
def test_admin_routes_are_for_administrators(db, monkeypatch, method, path):
    anon = _client(monkeypatch, auth_disabled=False)
    kw = {"json": {"clientId": CLIENT_ID, "clientSecret": SECRET}} if method == "put" else {}
    assert getattr(anon, method)(path, **kw).status_code == 401
    monkeypatch.setattr("api.auth.current_user",
                        lambda request: {"email": "member@easyskill.com", "name": "Member"})
    assert getattr(anon, method)(path, **kw).status_code == 403


def test_saving_a_client_never_echoes_the_secret(db, monkeypatch):
    admin = _client(monkeypatch, auth_disabled=True)
    r = admin.put("/api/admin/google-docs/client", json={"clientId": CLIENT_ID, "clientSecret": SECRET})
    assert r.status_code == 200
    assert SECRET not in r.text
    assert r.json()["client"]["source"] == "panel"


def test_a_malformed_client_id_is_explained(db, monkeypatch):
    admin = _client(monkeypatch, auth_disabled=True)
    r = admin.put("/api/admin/google-docs/client", json={"clientId": "not-an-id", "clientSecret": SECRET})
    assert r.status_code == 400
    assert "apps.googleusercontent.com" in r.json()["detail"]


def test_cancelling_on_google_returns_to_integrations_with_the_reason(db, monkeypatch):
    admin = _client(monkeypatch, auth_disabled=True)
    monkeypatch.setattr("api.google_docs_api.settings",
                        dataclasses.replace(real_settings, web_app_url="https://app.example.com"))
    r = admin.get("/api/admin/google-docs/callback?error=access_denied", follow_redirects=False)
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith("https://app.example.com/integrations?")
    assert "googleDocs=error" in loc and "cancelled" in loc


def test_a_forged_callback_connects_nothing(db, monkeypatch):
    admin = _client(monkeypatch, auth_disabled=True)
    r = admin.get("/api/admin/google-docs/callback?code=x&state=forged", follow_redirects=False)
    assert "googleDocs=error" in r.headers["location"]
    assert gd.status()["connected"] is False


def test_the_doc_html_carries_styles_docs_will_keep(db):
    from api.publish_api import _to_docs_html

    html = _to_docs_html(_report())
    assert "DRAFT — NOT APPROVED" in html
    assert 'page-break-before:always' in html
    assert "<table style=" in html and "<td style=" in html and "<th style=" in html
    assert "<style>" not in html, "Docs drops most of a stylesheet"
    assert "<h2>1. Mining</h2>" in html


def test_sending_a_report_before_connecting_is_a_clear_400(db, monkeypatch):
    from publish.store import create_report

    admin = _client(monkeypatch, auth_disabled=True)
    rep = create_report("2026-Q3", use_llm=False)
    r = admin.get(f"/api/publish/reports/{rep['id']}/google-docs")
    assert r.status_code == 200 and r.json() == {"connected": False, "account": None, "export": None}
    r = admin.post(f"/api/publish/reports/{rep['id']}/google-docs")
    assert r.status_code == 400
    assert "Integrations" in r.json()["detail"]


def test_deleting_a_report_forgets_its_doc(db, google, monkeypatch):
    from publish.store import create_report

    _connect(google)
    rep = create_report("2026-Q3", use_llm=False)
    gd.export(rep, "<p>x</p>", by="x", http=google)
    admin = _client(monkeypatch, auth_disabled=True)
    assert admin.delete(f"/api/publish/reports/{rep['id']}").status_code == 200
    assert gd.export_record(rep["id"]) is None
