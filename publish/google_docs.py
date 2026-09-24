"""Send a quarterly report to Google Docs.

The report is written and approved in MIOS, but it is read, commented on and
passed around in Google Docs — that is where Easy Skill already works. This
puts the report there as a real Google Doc (headings, lists, tables), and keeps
putting it in the *same* Doc on later exports so a link shared once stays good.

Decisions worth stating, because each has a plausible-looking alternative:

**A connected Google account, not a service account.** Service accounts have
had no Drive storage since 2023, so every Doc one creates fails with
`storageQuotaExceeded` unless the Workspace uses shared drives and delegation.
An administrator connecting their own account once avoids all of that, and the
Docs belong to a person who can share them.

**`drive.file`, not `drive` or `documents`.** MIOS can see only the files it
created itself — nothing else in the connected Drive. It is also the one Drive
scope Google does not treat as sensitive, so the app needs no review.

**HTML converted by Drive, not the Docs API.** Uploading the report as HTML with
a Google Docs MIME type makes Drive build the Doc, tables included. Building it
through `documents.batchUpdate` means computing every character index by hand
for the same result.

**Re-export replaces the Doc's contents.** Drive's documented behaviour for an
update with media. The Doc keeps its link, sharing and comments thread, but
edits made inside Google Docs are overwritten — the panel says so before it
happens. MIOS remains the place the report is edited.

Keys follow the same rules as every other integration: entered in the panel
(encrypted with `MIOS_CREDENTIAL_KEY`) or taken from the environment, the panel
winning. With neither, the Google client used for sign-in is reused, since it
already lives in Easy Skill's Google Cloud project.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import requests

from loader.db import connect

log = logging.getLogger(__name__)

#: Names in `llm_credentials` — encrypted. The client ID is not a secret and
#: sits in `kv_store`.
SECRET_NAME = "google_docs_client_secret"
REFRESH_NAME = "google_docs_refresh_token"
CLIENT_ID_ENV = "GOOGLE_DOCS_CLIENT_ID"
CLIENT_SECRET_ENV = "GOOGLE_DOCS_CLIENT_SECRET"
REDIRECT_ENV = "GOOGLE_DOCS_REDIRECT_URI"

CLIENT_ID_KEY = "google_docs:client_id"
ACCOUNT_KEY = "google_docs:account"
FOLDER_KEY = "google_docs:folder"
STATE_PREFIX = "google_docs:state:"

CALLBACK_PATH = "/api/admin/google-docs/callback"
FOLDER_NAME = "MIOS Quarterly Reports"
SCOPES = "openid email https://www.googleapis.com/auth/drive.file"
#: Long enough to find the consent screen and click through; short enough that
#: a stale link cannot be replayed later.
STATE_TTL = timedelta(minutes=15)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
DOC_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"
TIMEOUT = 30

_DDL = """
CREATE TABLE IF NOT EXISTS report_exports (
    report_id   TEXT NOT NULL,
    provider    TEXT NOT NULL,
    file_id     TEXT NOT NULL,
    url         TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    exported_by TEXT,
    PRIMARY KEY (report_id, provider)
)
"""


class GoogleDocsError(RuntimeError):
    """Google answered, but not with something usable."""


class GoogleDocsNotConfigured(GoogleDocsError):
    """No client to sign in with, or no account connected yet."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Stored settings
# --------------------------------------------------------------------------


def _kv_get(key: str, target) -> Any:
    try:
        with connect(target, readonly=True) as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.debug("google_docs: could not read %s (%s)", key, exc)
        return None
    if not row or not row["value"]:
        return None
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return None


def _kv_set(key: str, value: Any, target) -> None:
    body = json.dumps(value)
    with connect(target) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO kv_store (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = ?",
            (key, body, body),
        )


def _kv_delete(key: str, target) -> None:
    try:
        with connect(target) as conn:
            conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))
    except Exception as exc:  # noqa: BLE001 - nothing to delete
        log.debug("google_docs: could not delete %s (%s)", key, exc)


def _sign_in_client() -> tuple[str, str]:
    from config.settings import settings

    return (settings.google_client_id or "").strip(), (settings.google_client_secret or "").strip()


def client(target=None) -> dict[str, str]:
    """The OAuth client in play, and where it came from.

    Entered in the panel, else GOOGLE_DOCS_CLIENT_ID/SECRET, else the sign-in
    client. ID and secret always come from the same place — a panel ID paired
    with an environment secret would be two different clients.
    """
    from loader.credentials import stored_key

    panel_id = _kv_get(CLIENT_ID_KEY, target) or ""
    panel_secret = stored_key(SECRET_NAME, target) or ""
    if panel_id and panel_secret:
        return {"id": panel_id, "secret": panel_secret, "source": "panel"}
    env_id = os.environ.get(CLIENT_ID_ENV, "").strip()
    env_secret = os.environ.get(CLIENT_SECRET_ENV, "").strip()
    if env_id and env_secret:
        return {"id": env_id, "secret": env_secret, "source": "environment"}
    sign_in_id, sign_in_secret = _sign_in_client()
    if sign_in_id and sign_in_secret:
        return {"id": sign_in_id, "secret": sign_in_secret, "source": "sign-in"}
    return {"id": "", "secret": "", "source": "none"}


def redirect_uri() -> str:
    """Where Google sends the administrator back to after consent.

    Next to the sign-in callback, which is already registered with Google and
    known to reach this API, so the only new thing to add in Google Cloud is
    this one URI.
    """
    explicit = os.environ.get(REDIRECT_ENV, "").strip()
    if explicit:
        return explicit
    from config.settings import settings

    parts = urlsplit(settings.oauth_redirect_uri)
    return f"{parts.scheme}://{parts.netloc}{CALLBACK_PATH}"


def set_client(client_id: str, client_secret: str, *, changed_by: str, target=None) -> None:
    from loader.credentials import set_key

    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id or not client_secret:
        raise ValueError("Both the client ID and the client secret are required.")
    set_key(SECRET_NAME, client_secret, changed_by=changed_by, target=target)
    _kv_set(CLIENT_ID_KEY, client_id, target)
    log.info("google_docs: %s set the OAuth client (%s)", changed_by, client_id[:12])


def clear_client(target=None) -> bool:
    from loader.credentials import clear_key

    had = bool(_kv_get(CLIENT_ID_KEY, target))
    _kv_delete(CLIENT_ID_KEY, target)
    return clear_key(SECRET_NAME, target) or had


def account(target=None) -> dict[str, Any] | None:
    return _kv_get(ACCOUNT_KEY, target)


def status(target=None) -> dict[str, Any]:
    """Everything the Integrations panel shows. Never a secret."""
    from loader.credentials import available, stored_key

    c = client(target)
    acct = account(target)
    return {
        "client": {"source": c["source"], "id": c["id"] or None},
        "canStoreKey": available(),
        "clientEnv": [CLIENT_ID_ENV, CLIENT_SECRET_ENV],
        "redirectUri": redirect_uri(),
        "scopes": SCOPES.split(),
        "account": acct,
        # An account row without a readable token — a rotated MIOS_CREDENTIAL_KEY,
        # or a row half-written — needs reconnecting, and should say so.
        "connected": bool(acct and stored_key(REFRESH_NAME, target)),
        "folder": _kv_get(FOLDER_KEY, target),
    }


# --------------------------------------------------------------------------
# Connecting an account
# --------------------------------------------------------------------------


def start_connect(by: str, target=None) -> str:
    """The Google consent URL. `state` ties the answer to this request."""
    c = client(target)
    if not c["id"]:
        raise GoogleDocsNotConfigured(
            "No Google OAuth client. Enter a client ID and secret, or set "
            f"{CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} on the server.")
    state = secrets.token_urlsafe(24)
    _kv_set(STATE_PREFIX + state,
            {"by": by, "expires": (datetime.now(timezone.utc) + STATE_TTL).isoformat()}, target)
    return AUTH_URL + "?" + urlencode({
        "client_id": c["id"],
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        # offline + consent: Google only returns a refresh token on a consent
        # screen, and a reconnect without one would leave nothing to store.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })


def finish_connect(code: str, state: str, *, http=requests, target=None) -> dict[str, Any]:
    """Exchange the code, store the refresh token, and record whose Drive it is."""
    from loader.credentials import set_key

    saved = _kv_get(STATE_PREFIX + (state or ""), target) if state else None
    if not saved:
        raise GoogleDocsError("That sign-in link is not one MIOS issued, or it was already used. "
                              "Start again from Integrations.")
    _kv_delete(STATE_PREFIX + state, target)
    if datetime.fromisoformat(saved["expires"]) < datetime.now(timezone.utc):
        raise GoogleDocsError("That sign-in link expired. Start again from Integrations.")

    c = client(target)
    resp = http.post(TOKEN_URL, data={
        "code": code, "client_id": c["id"], "client_secret": c["secret"],
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
    }, timeout=TIMEOUT)
    body = _json(resp)
    if resp.status_code != 200:
        raise GoogleDocsError(f"Google refused the sign-in: {body.get('error_description') or body.get('error') or resp.status_code}")
    granted = set((body.get("scope") or "").split())
    if "https://www.googleapis.com/auth/drive.file" not in granted:
        raise GoogleDocsError("Google Drive access was not granted. Connect again and leave "
                              "the Drive box ticked on the consent screen.")
    refresh = body.get("refresh_token")
    if not refresh:
        raise GoogleDocsError("Google did not return a long-lived token. Connect again.")

    email = None
    info = http.get(USERINFO_URL, headers={"Authorization": f"Bearer {body['access_token']}"},
                    timeout=TIMEOUT)
    if info.status_code == 200:
        email = _json(info).get("email")

    set_key(REFRESH_NAME, refresh, changed_by=saved["by"], target=target)
    previous = account(target)
    acct = {"email": email, "connectedAt": _now(), "connectedBy": saved["by"]}
    _kv_set(ACCOUNT_KEY, acct, target)
    # A different account cannot see the old account's folder under drive.file.
    if previous and previous.get("email") != email:
        _kv_delete(FOLDER_KEY, target)
    log.info("google_docs: %s connected Google Drive as %s", saved["by"], email)
    return acct


def disconnect(*, http=requests, target=None) -> bool:
    """Revoke MIOS's access at Google and forget the token."""
    from loader.credentials import clear_key, stored_key

    token = stored_key(REFRESH_NAME, target)
    if token:
        try:
            http.post(REVOKE_URL, data={"token": token}, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - forget it locally regardless
            log.warning("google_docs: revoke failed (%s) — forgetting the token anyway", exc)
    removed = clear_key(REFRESH_NAME, target)
    had = account(target) is not None
    _kv_delete(ACCOUNT_KEY, target)
    _kv_delete(FOLDER_KEY, target)
    return removed or had


# --------------------------------------------------------------------------
# Exporting
# --------------------------------------------------------------------------


def _json(resp) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _google_error(resp, doing: str) -> GoogleDocsError:
    err = _json(resp).get("error")
    message = err.get("message") if isinstance(err, dict) else (err or "")
    if resp.status_code == 403 and "has not been used" in (message or ""):
        return GoogleDocsError("The Google Drive API is not enabled in this Google Cloud "
                               "project. Enable it, wait a minute, and try again.")
    return GoogleDocsError(f"Google Drive could not {doing} ({resp.status_code}): {message}".rstrip(": "))


def _access_token(http, target) -> str:
    from loader.credentials import stored_key

    refresh = stored_key(REFRESH_NAME, target)
    if not refresh:
        raise GoogleDocsNotConfigured("Google Docs is not connected. An administrator can "
                                      "connect it under Admin › Integrations.")
    c = client(target)
    resp = http.post(TOKEN_URL, data={
        "client_id": c["id"], "client_secret": c["secret"],
        "refresh_token": refresh, "grant_type": "refresh_token",
    }, timeout=TIMEOUT)
    body = _json(resp)
    if resp.status_code != 200:
        if body.get("error") == "invalid_grant":
            raise GoogleDocsNotConfigured("Google access has expired or was revoked. Reconnect "
                                          "Google Docs under Admin › Integrations.")
        raise GoogleDocsError(f"Google refused the stored token: {body.get('error') or resp.status_code}")
    return str(body["access_token"])


def _multipart(metadata: dict[str, Any], html: str) -> tuple[bytes, str]:
    boundary = "mios-" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\nContent-Type: text/html; charset=UTF-8\r\n\r\n"
        f"{html}\r\n--{boundary}--\r\n"
    ).encode("utf-8")
    return body, f"multipart/related; boundary={boundary}"


def _folder(http, auth: dict[str, str], target) -> str:
    """MIOS's own folder in the connected Drive, created on first use.

    Its own rather than one an administrator picks: under `drive.file` MIOS
    cannot write into a folder it did not create. It can be moved or shared
    freely once it exists.
    """
    saved = _kv_get(FOLDER_KEY, target)
    if saved and saved.get("id"):
        resp = http.get(f"{FILES_URL}/{saved['id']}", params={"fields": "id,trashed"},
                        headers=auth, timeout=TIMEOUT)
        if resp.status_code == 200 and not _json(resp).get("trashed"):
            return str(saved["id"])
    resp = http.post(FILES_URL, params={"fields": "id,webViewLink"}, headers=auth,
                     json={"name": FOLDER_NAME, "mimeType": FOLDER_MIME}, timeout=TIMEOUT)
    if resp.status_code != 200:
        raise _google_error(resp, "create the MIOS folder")
    body = _json(resp)
    _kv_set(FOLDER_KEY, {"id": body["id"], "url": body.get("webViewLink")}, target)
    return str(body["id"])


def _ensure_table(conn) -> None:
    conn.execute(_DDL)


def export_record(report_id: str, target=None) -> dict[str, Any] | None:
    try:
        with connect(target, readonly=True) as conn:
            row = conn.execute(
                "SELECT file_id, url, exported_at, exported_by FROM report_exports "
                "WHERE report_id = ? AND provider = 'google_docs'", (report_id,)).fetchone()
    except Exception:  # noqa: BLE001 - table not created yet
        return None
    if not row:
        return None
    return {"fileId": row["file_id"], "url": row["url"],
            "exportedAt": row["exported_at"], "exportedBy": row["exported_by"]}


def forget_export(report_id: str, target=None) -> None:
    try:
        with connect(target) as conn:
            conn.execute("DELETE FROM report_exports WHERE report_id = ?", (report_id,))
    except Exception as exc:  # noqa: BLE001 - nothing was ever exported
        log.debug("google_docs: no export rows to forget (%s)", exc)


def doc_name(report: dict[str, Any]) -> str:
    suffix = "" if report.get("status") == "approved" else " (DRAFT)"
    return f"{report['title']}{suffix}"


def export(report: dict[str, Any], html: str, *, by: str, http=requests,
           target=None) -> dict[str, Any]:
    """Put the report in Google Docs: its existing Doc if it has one, else a new one."""
    if len(html.encode("utf-8")) > 5 * 1024 * 1024:
        raise GoogleDocsError("The report is larger than Google's 5 MB upload limit.")
    auth = {"Authorization": f"Bearer {_access_token(http, target)}"}
    previous = export_record(report["id"], target)
    fields = {"fields": "id,webViewLink"}

    resp = None
    if previous:
        body, ctype = _multipart({"name": doc_name(report)}, html)
        resp = http.patch(f"{UPLOAD_URL}/{previous['fileId']}",
                          params={"uploadType": "multipart", **fields},
                          headers={**auth, "Content-Type": ctype}, data=body, timeout=TIMEOUT)
        # Gone: deleted in Drive, or created by an account since disconnected.
        # A fresh Doc is the useful answer; the old link is reported as replaced.
        if resp.status_code in (403, 404):
            log.info("google_docs: Doc %s for %s is no longer reachable (%s) — creating a new one",
                     previous["fileId"], report["id"], resp.status_code)
            resp = None
        elif resp.status_code != 200:
            raise _google_error(resp, "update the Doc")

    created = resp is None
    if created:
        folder = _folder(http, auth, target)
        body, ctype = _multipart(
            {"name": doc_name(report), "mimeType": DOC_MIME, "parents": [folder]}, html)
        resp = http.post(UPLOAD_URL, params={"uploadType": "multipart", **fields},
                         headers={**auth, "Content-Type": ctype}, data=body, timeout=TIMEOUT)
        if resp.status_code != 200:
            raise _google_error(resp, "create the Doc")

    meta = _json(resp)
    file_id = str(meta["id"])
    url = meta.get("webViewLink") or f"https://docs.google.com/document/d/{file_id}/edit"
    stamp = _now()
    with connect(target) as conn:
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO report_exports (report_id, provider, file_id, url, exported_at, exported_by) "
            "VALUES (?, 'google_docs', ?, ?, ?, ?) "
            "ON CONFLICT (report_id, provider) DO UPDATE SET "
            "file_id = ?, url = ?, exported_at = ?, exported_by = ?",
            (report["id"], file_id, url, stamp, by, file_id, url, stamp, by),
        )
    log.info("google_docs: %s %s %s as %s", by, "created" if created else "updated",
             report["id"], file_id)
    return {"fileId": file_id, "url": url, "exportedAt": stamp, "exportedBy": by,
            "created": created, "replacedLink": bool(previous and created)}


__all__ = [
    "GoogleDocsError", "GoogleDocsNotConfigured", "account", "clear_client", "client",
    "disconnect", "doc_name", "export", "export_record", "finish_connect", "forget_export",
    "redirect_uri", "set_client", "start_connect", "status",
]
