"""Admin endpoints for what the collectors need to be told.

The Apify token, which actor reads which job board, the ASX companies to
follow, and extra news feeds. All of it lives in `loader.source_config`; this
file is the HTTP face of it.

Every response is the status object the panels render, so the token — which is
the credential — can never appear in one.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from api.admin_api import require_admin
from loader import source_config
from loader.credentials import CredentialError, CredentialsLocked

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/source-config", tags=["source-config"])


def _with(note: str, **extra: Any) -> dict[str, Any]:
    return {**source_config.status(), "note": note, **extra}


@router.get("")
def get_status(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return source_config.status()


# ---------- Apify ----------


@router.put("/apify/token")
def put_token(payload: dict[str, Any] = Body(...),
              user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        source_config.set_apify_token(str(payload.get("token") or ""), changed_by=user["email"])
    except source_config.SourceConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CredentialsLocked as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except CredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("source_config: %s stored an Apify token", user["email"])
    return _with("Token saved. Test it, then name an actor for each board you want.")


@router.delete("/apify/token")
def delete_token(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    removed = source_config.clear_apify_token()
    log.info("source_config: %s removed the Apify token (had one: %s)", user["email"], removed)
    return _with("Token removed. The Apify boards will not be read until one is added."
                 if removed else "There was no stored token.")


@router.post("/apify/test")
def test_token(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Ask Apify whose token this is. Runs no actor and costs nothing."""
    try:
        result = source_config.test_apify_token()
    except source_config.SourceConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _with(result["detail"], testOk=result["ok"])


@router.put("/apify/boards/{source_id}")
def put_board(source_id: str, payload: dict[str, Any] = Body(...),
              user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    actor = str(payload.get("actor") or "")
    try:
        source_config.set_board(source_id, actor, payload.get("input"),
                                changed_by=user["email"])
    except source_config.SourceConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not actor.strip():
        return _with("Actor removed. That board is no longer read.")
    if not source_config.apify_token():
        return _with("Saved. Add an Apify token above and this board is read from the next run.")
    return _with("Saved. This board is read from the next run; it can be switched off "
                 "under Data sources.")


@router.put("/apify/search")
def put_search(payload: dict[str, Any] = Body(...),
               user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """The default search every board uses. Empty returns to the built-in one."""
    raw = payload.get("keywords")
    try:
        keywords = source_config.set_apify_search(raw, changed_by=user["email"])
    except source_config.SourceConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if raw is None or not str(raw).strip():
        return _with(f"Back to the built-in search: {keywords}.")
    return _with("Saved. Every board without search settings of its own uses it from the next run.")


@router.put("/apify/run")
def put_run(payload: dict[str, Any] = Body(...),
            user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """The most one run of an actor may be charged. Empty returns to the default."""
    raw = payload.get("maxChargeUsd")
    try:
        value = source_config.set_apify_max_charge(raw, changed_by=user["email"])
    except source_config.SourceConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if raw is None or not str(raw).strip():
        return _with(f"Back to the default of ${value:.2f} a run.")
    return _with(f"A run of an actor now stops being charged at ${value:.2f}.")


# ---------- ASX companies ----------


@router.put("/asx")
def put_asx(payload: dict[str, Any] = Body(...),
            user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        tickers = source_config.set_asx_tickers(payload.get("tickers"), changed_by=user["email"])
    except source_config.SourceConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not tickers:
        return _with("Back to the built-in list of companies.")
    return _with(f"Following {len(tickers)} compan{'y' if len(tickers) == 1 else 'ies'} "
                 "from the next run.")


# ---------- Custom news feeds ----------


@router.put("/feeds")
def put_feeds(payload: dict[str, Any] = Body(...),
              user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        feeds = source_config.set_custom_feeds(payload.get("feeds"), changed_by=user["email"])
    except source_config.SourceConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not feeds:
        return _with("No custom feeds. The catalogued publications are unaffected.")
    return _with(f"Saved {len(feeds)} feed{'' if len(feeds) == 1 else 's'}. "
                 "Read from the next run, as “Custom RSS feeds” on the Sources tab.")


@router.post("/feeds/check")
def check_feed(payload: dict[str, Any] = Body(...),
               user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Fetch one address and say whether it is a readable feed. Saves nothing."""
    try:
        return source_config.check_feed(str(payload.get("url") or ""))
    except source_config.SourceConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
