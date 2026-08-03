"""Tests for Google Sign-In gating (api.auth + api.server).

No live Google: `authorize_claims` is a pure policy function over already-verified
ID-token claims, and the endpoint tests drive the app through FastAPI's TestClient.
"""
from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from api.auth import (
    NotAuthorised,
    authorize_claims,
    check_google_client_id,
    safe_next_path,
)
from config.settings import settings as real_settings

WORKSPACE_CLAIMS = {
    "sub": "1234567890",
    "email": "Priya.Sharma@easyskill.com",
    "email_verified": True,
    "name": "Priya Sharma",
    "picture": "https://lh3.googleusercontent.com/a/x",
    "hd": "easyskill.com",
}
GMAIL_CLAIMS = {
    "sub": "999",
    "email": "someone@gmail.com",
    "email_verified": True,
    "name": "Someone",
}


@pytest.fixture
def configure(monkeypatch):
    """Swap in a Settings clone with the given auth overrides."""
    def _apply(**overrides):
        patched = dataclasses.replace(real_settings, **overrides)
        monkeypatch.setattr("api.auth.settings", patched)
        return patched
    return _apply


# ---------- domain policy ----------


def test_workspace_account_is_allowed(configure):
    configure(allowed_google_domain="easyskill.com", allowed_emails=())
    user = authorize_claims(WORKSPACE_CLAIMS)
    assert user["email"] == "priya.sharma@easyskill.com"  # normalised to lowercase
    assert user["name"] == "Priya Sharma"
    assert user["domain"] == "easyskill.com"


def test_personal_gmail_is_rejected_when_domain_configured(configure):
    configure(allowed_google_domain="easyskill.com", allowed_emails=())
    with pytest.raises(NotAuthorised, match="not a easyskill.com account"):
        authorize_claims(GMAIL_CLAIMS)


def test_other_workspace_domain_is_rejected(configure):
    configure(allowed_google_domain="easyskill.com", allowed_emails=())
    with pytest.raises(NotAuthorised):
        authorize_claims({**WORKSPACE_CLAIMS, "email": "x@rival.com", "hd": "rival.com"})


def test_hd_claim_is_what_counts_not_the_email_suffix(configure):
    """A gmail user can't get in by having an easyskill.com-looking address.

    The `hd` claim is issued by Google for Workspace accounts; the email string
    alone proves nothing, so a mismatch must be refused.
    """
    configure(allowed_google_domain="easyskill.com", allowed_emails=())
    spoofed = {**GMAIL_CLAIMS, "email": "ceo@easyskill.com"}  # no hd claim
    with pytest.raises(NotAuthorised):
        authorize_claims(spoofed)


# ---------- allowlist + verification ----------


def test_allowlisted_email_bypasses_domain_check(configure):
    configure(allowed_google_domain="easyskill.com", allowed_emails=("someone@gmail.com",))
    user = authorize_claims(GMAIL_CLAIMS)
    assert user["email"] == "someone@gmail.com"
    assert user["domain"] is None


def test_allowlist_is_case_insensitive(configure):
    configure(allowed_google_domain="easyskill.com", allowed_emails=("SomeOne@Gmail.com",))
    assert authorize_claims(GMAIL_CLAIMS)["email"] == "someone@gmail.com"


def test_unverified_email_is_rejected_even_on_the_right_domain(configure):
    configure(allowed_google_domain="easyskill.com", allowed_emails=())
    with pytest.raises(NotAuthorised, match="not a verified"):
        authorize_claims({**WORKSPACE_CLAIMS, "email_verified": False})


def test_missing_email_is_rejected(configure):
    configure(allowed_google_domain="", allowed_emails=())
    with pytest.raises(NotAuthorised, match="did not return an email"):
        authorize_claims({"sub": "1", "email_verified": True})


def test_open_mode_allows_any_verified_account(configure):
    # Nothing configured => open. Permitted, but the module logs a warning.
    configure(allowed_google_domain="", allowed_emails=())
    assert authorize_claims(GMAIL_CLAIMS)["email"] == "someone@gmail.com"


# ---------- open-redirect guard ----------


@pytest.mark.parametrize("raw", [
    "https://evil.example/steal",
    "//evil.example",
    "http://evil.example",
    None,
    "",
    "relative/path",
])
def test_safe_next_path_rejects_offsite_targets(raw):
    assert safe_next_path(raw) == "/"


@pytest.mark.parametrize("raw", ["/", "/monitor/digest", "/watchlist?tier=A"])
def test_safe_next_path_keeps_in_app_paths(raw):
    assert safe_next_path(raw) == raw


# ---------- client-id sanity checks ----------

GOOD_ID = "478433912962-mv1rs0nqc6pmgro1tp663m92mj0s6adr.apps.googleusercontent.com"


def test_wellformed_client_id_has_no_problems():
    assert check_google_client_id(GOOD_ID) == []


def test_blank_client_id_is_not_flagged_here():
    # "not configured" is reported separately; don't double-warn.
    assert check_google_client_id("") == []


def test_doubled_suffix_is_caught():
    """The mistake that actually happened: pasting over the .env.example
    placeholder left `.apps.googleusercontent.com` on the end twice, and Google
    answered only with 'invalid_client'."""
    problems = check_google_client_id(GOOD_ID + ".apps.googleusercontent.com")
    assert any("appears 2 times" in p for p in problems)


def test_missing_suffix_is_caught():
    assert any("does not end with" in p for p in check_google_client_id("478433912962-abc"))


def test_quoted_value_is_caught():
    assert any("surrounding quotes" in p for p in check_google_client_id(f'"{GOOD_ID}"'))


def test_wrong_shape_is_caught():
    problems = check_google_client_id("not-a-real-id.apps.googleusercontent.com")
    assert any("does not match" in p for p in problems)


# ---------- account-chooser hint ----------


def _login_query(monkeypatch, **overrides):
    """Follow /auth/login one hop and return the Google authorization params."""
    from urllib.parse import parse_qs, urlparse

    import api.auth as auth_mod

    patched = dataclasses.replace(
        real_settings,
        google_client_id="123-abc.apps.googleusercontent.com",
        google_client_secret="secret",
        auth_disabled=False,
        **overrides,
    )
    monkeypatch.setattr(auth_mod, "settings", patched)
    auth_mod.oauth.register(
        name="google", client_id=patched.google_client_id,
        client_secret=patched.google_client_secret,
        server_metadata_url=auth_mod.GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
        overwrite=True,
    )
    from api.server import app
    res = TestClient(app).get("/auth/login", follow_redirects=False)
    return parse_qs(urlparse(res.headers["location"]).query)


def test_login_sends_hd_hint_when_domain_is_the_only_rule(monkeypatch):
    q = _login_query(monkeypatch, allowed_google_domain="easyskill.com", allowed_emails=())
    assert q["hd"] == ["easyskill.com"]


def test_login_omits_hd_hint_when_emails_are_allowlisted(monkeypatch):
    """Otherwise Google's chooser filters out the very account we allowlisted."""
    q = _login_query(
        monkeypatch,
        allowed_google_domain="easyskill.com",
        allowed_emails=("dev@gmail.com",),
    )
    assert "hd" not in q


# ---------- endpoint gating ----------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("api.auth.settings", dataclasses.replace(real_settings, auth_disabled=False))
    from api.server import app
    return TestClient(app)


def test_digest_requires_sign_in(client):
    res = client.get("/api/digest")
    assert res.status_code == 401
    assert "Sign in" in res.json()["detail"]


def test_health_is_public_and_leaks_no_data(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    # Regression: /api/health used to report the db path and signal counts to
    # anyone who asked. Liveness must not describe the data.
    assert "db" not in body
    assert "signals" not in body


def test_me_reports_signed_out_without_a_session(client):
    res = client.get("/api/me")
    assert res.status_code == 200
    body = res.json()
    assert body["authenticated"] is False
    assert body["user"] is None
    assert "loginUrl" in body


def _session_cookie(data: dict) -> str:
    """Forge the cookie exactly as Starlette's SessionMiddleware signs it.

    Lets us assert that a *valid* session actually grants access, without
    standing up a fake Google to complete a real OAuth round trip.
    """
    import base64
    import json

    import itsdangerous

    signer = itsdangerous.TimestampSigner(str(real_settings.session_secret))
    payload = base64.b64encode(json.dumps(data).encode())
    return signer.sign(payload).decode()


def test_valid_session_grants_access_to_digest(client):
    from api.auth import SESSION_USER_KEY

    user = {"sub": "1", "email": "priya@easyskill.com", "name": "Priya", "picture": None,
            "domain": "easyskill.com"}
    client.cookies.set("session", _session_cookie({SESSION_USER_KEY: user}))

    me = client.get("/api/me").json()
    assert me["authenticated"] is True
    assert me["user"]["email"] == "priya@easyskill.com"

    assert client.get("/api/digest").status_code == 200


def test_tampered_session_cookie_is_rejected(client):
    """A forged cookie without a valid signature must not authenticate."""
    client.cookies.set("session", "eyJ1c2VyIjp7ImVtYWlsIjoiaGFja2VyQGV2aWwuY29tIn19.fake.sig")
    assert client.get("/api/me").json()["authenticated"] is False
    assert client.get("/api/digest").status_code == 401


def test_logout_expires_the_session_cookie(client):
    """Logout must tell the browser to drop the cookie.

    The session is a stateless signed cookie, so sign-out is a client-side
    delete: there is no server-side record to revoke. A cookie already copied
    off the machine stays valid until SESSION_MAX_AGE elapses — which is why
    that default is short (12h) rather than weeks.
    """
    from api.auth import SESSION_USER_KEY

    user = {"sub": "1", "email": "priya@easyskill.com", "name": "Priya", "picture": None,
            "domain": "easyskill.com"}
    client.cookies.set("session", _session_cookie({SESSION_USER_KEY: user}))
    assert client.get("/api/digest").status_code == 200

    res = client.post("/auth/logout")
    assert res.json() == {"ok": True}
    set_cookie = res.headers["set-cookie"]
    assert "session=null" in set_cookie
    assert "expires=Thu, 01 Jan 1970" in set_cookie

    # And with the cookie actually gone (as a browser would leave it), access stops.
    client.cookies.clear()
    assert client.get("/api/digest").status_code == 401


def test_auth_disabled_opens_the_api(monkeypatch):
    """The dev bypass must actually bypass — and announce itself."""
    monkeypatch.setattr("api.auth.settings", dataclasses.replace(real_settings, auth_disabled=True))
    from api.server import app
    c = TestClient(app)
    assert c.get("/api/digest").status_code == 200
    assert c.get("/api/me").json()["authDisabled"] is True
