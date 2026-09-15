"""Admin endpoints for filling the watchlist from HubSpot.

Every response is the same status object the panel renders, so the service key
can never appear in one: it is written here and read only by the sync.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from api.admin_api import require_admin
from loader import hubspot_watchlist as hubspot
from loader.credentials import CredentialError, CredentialsLocked, clear_key, set_key

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/hubspot", tags=["hubspot"])


def _raise(exc: Exception) -> None:
    if isinstance(exc, hubspot.HubSpotNotConfigured):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, hubspot.HubSpotError):
        # 502: our request was fine, HubSpot's answer was not usable.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise exc


@router.get("")
def get_status(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return hubspot.status()


@router.put("/key")
def put_key(payload: dict[str, Any] = Body(...),
            user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    key = str(payload.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="A service key is required.")
    try:
        set_key(hubspot.KEY_NAME, key, changed_by=user["email"])
    except CredentialsLocked as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except CredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**hubspot.status(), "note": "Service key saved. Load the fields to check it works."}


@router.delete("/key")
def delete_key(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    removed = clear_key(hubspot.KEY_NAME)
    log.info("hubspot: %s removed the stored service key (had one: %s)", user["email"], removed)
    return {**hubspot.status(),
            "note": "Stored service key removed." if removed else "There was no stored key."}


@router.get("/properties")
def get_properties(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Company fields from HubSpot. Also the quickest check that the key works."""
    try:
        client = hubspot.HubSpotClient(hubspot.api_key())
        properties = client.company_properties()
    except Exception as exc:  # noqa: BLE001 - mapped to an HTTP status
        _raise(exc)
    return {
        "properties": properties,
        #: Fields with fixed options, which is what a tier field is.
        "tierCandidates": [p for p in properties if p["options"]],
    }


@router.put("/mapping")
def put_mapping(payload: dict[str, Any] = Body(...),
                user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        hubspot.set_mapping(payload, changed_by=user["email"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return hubspot.status()


@router.post("/sync")
def post_sync(payload: dict[str, Any] = Body(default={}),
              user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Preview (`dryRun: true`) or apply a sync."""
    dry_run = bool(payload.get("dryRun", True))
    try:
        result = hubspot.sync(changed_by=user["email"], dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - mapped to an HTTP status
        _raise(exc)
    return {**hubspot.status(), "result": result}
