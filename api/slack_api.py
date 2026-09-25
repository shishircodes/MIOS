"""Admin endpoints for the Slack digest: the webhook, on/off, and a test post.

Every response is the status object the Integrations panel renders, so the
webhook — which is itself the credential — can never appear in one.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from api.admin_api import require_admin
from delivery import slack_config
from loader.credentials import CredentialError, CredentialsLocked

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/slack", tags=["slack"])


@router.get("")
def get_status(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return slack_config.status()


@router.put("/webhook")
def put_webhook(payload: dict[str, Any] = Body(...),
                user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        slack_config.set_webhook(str(payload.get("url") or ""), changed_by=user["email"])
    except slack_config.SlackConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CredentialsLocked as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except CredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**slack_config.status(), "note": "Webhook saved. Send a test message to check it."}


@router.delete("/webhook")
def delete_webhook(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    removed = slack_config.clear_webhook()
    log.info("slack: %s removed the stored webhook (had one: %s)", user["email"], removed)
    return {**slack_config.status(),
            "note": "Stored webhook removed." if removed else "There was no stored webhook."}


@router.put("/settings")
def put_settings(payload: dict[str, Any] = Body(...),
                 user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    if not isinstance(payload.get("enabled"), bool):
        raise HTTPException(status_code=400, detail="`enabled` must be true or false.")
    slack_config.set_enabled(payload["enabled"], changed_by=user["email"])
    return {**slack_config.status(),
            "note": ("The digest will be posted after each run." if payload["enabled"]
                     else "Runs will no longer post the digest. It is still built and archived.")}


@router.post("/test")
def test_message(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Post a short test message now. Reports Slack's own answer when it fails."""
    try:
        result = slack_config.send_test(by=user["email"])
    except slack_config.SlackConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("slack: %s sent a test message (%s)", user["email"], result["detail"])
    return {**slack_config.status(),
            "note": ("Test message posted — check the channel." if result["ok"]
                     else f"The test message was not delivered: {result['detail']}"),
            "testOk": result["ok"]}
