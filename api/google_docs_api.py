"""Admin endpoints for connecting Google Docs, used by Mode Publish.

Every response is the status object the Integrations panel renders, so neither
the client secret nor the refresh token can appear in one.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from api.auth import check_google_client_id, require_admin
from config.settings import settings
from loader.credentials import CredentialError, CredentialsLocked
from publish import google_docs

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/google-docs", tags=["google-docs"])


@router.get("")
def get_status(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return google_docs.status()


@router.put("/client")
def put_client(payload: dict[str, Any] = Body(...),
               user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    client_id = str(payload.get("clientId") or "").strip()
    secret = str(payload.get("clientSecret") or "").strip()
    problems = check_google_client_id(client_id)
    if problems:
        raise HTTPException(status_code=400, detail="That client ID " + "; ".join(problems) + ".")
    try:
        google_docs.set_client(client_id, secret, changed_by=user["email"])
    except CredentialsLocked as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (CredentialError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**google_docs.status(), "note": "Client saved. Connect a Google account to use it."}


@router.delete("/client")
def delete_client(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    removed = google_docs.clear_client()
    log.info("google_docs: %s removed the stored client (had one: %s)", user["email"], removed)
    return {**google_docs.status(),
            "note": "Stored client removed." if removed else "There was no stored client."}


@router.post("/connect")
def connect(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """The Google consent URL. The browser goes there; Google comes back to /callback."""
    try:
        return {"url": google_docs.start_connect(user["email"])}
    except google_docs.GoogleDocsNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/callback", include_in_schema=False)
def callback(code: str = Query(""), state: str = Query(""), error: str = Query("")):
    """Google's redirect after the consent screen.

    No session dependency on purpose: the single-use `state`, issued only to an
    administrator by /connect, is what authorises this — and a session cookie
    may not travel on a cross-site redirect back from Google.
    """
    back = f"{settings.web_app_url}/integrations"
    if error:
        # "access_denied" is somebody pressing Cancel — say so plainly.
        reason = ("You cancelled on Google's consent screen." if error == "access_denied"
                  else f"Google returned: {error}")
        return RedirectResponse(back + "?" + urlencode({"googleDocs": "error", "reason": reason}))
    try:
        acct = google_docs.finish_connect(code, state)
    except google_docs.GoogleDocsError as exc:
        return RedirectResponse(back + "?" + urlencode({"googleDocs": "error", "reason": str(exc)}))
    except Exception as exc:  # noqa: BLE001 - never leave the admin on a JSON 500
        log.exception("google_docs: connect failed")
        return RedirectResponse(back + "?" + urlencode(
            {"googleDocs": "error", "reason": f"Connecting failed: {type(exc).__name__}"}))
    return RedirectResponse(back + "?" + urlencode(
        {"googleDocs": "connected", "account": acct.get("email") or ""}))


@router.delete("/connection")
def delete_connection(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    removed = google_docs.disconnect()
    log.info("google_docs: %s disconnected Google Docs (was connected: %s)", user["email"], removed)
    return {**google_docs.status(),
            "note": "Disconnected. Docs already created stay in that Google Drive."}
