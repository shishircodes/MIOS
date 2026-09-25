"""Slack incoming-webhook delivery."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


def post_message(webhook_url: str, text: str, timeout: float = 15.0) -> tuple[bool, str]:
    """POST a message to a Slack incoming webhook. Returns (delivered, detail).

    `detail` is what an administrator needs when it fails — Slack's own answer
    ("invalid_token", "channel_is_archived") rather than just a status code.
    Never raises: a Slack outage must not fail the pipeline run behind it.
    """
    if not webhook_url:
        log.error("slack: no webhook URL configured")
        return False, "no webhook URL configured"
    try:
        resp = requests.post(webhook_url, json={"text": text, "mrkdwn": True}, timeout=timeout)
    except requests.RequestException as exc:
        log.error("slack: request failed: %s", exc)
        return False, f"could not reach Slack ({type(exc).__name__})"
    if resp.status_code == 200:
        log.info("slack: delivered (%d chars)", len(text))
        return True, "delivered"
    body = (resp.text or "").strip()[:200]
    log.error("slack: Slack returned %d: %s", resp.status_code, body)
    return False, f"Slack answered {resp.status_code}: {body or 'no detail'}"


def post_digest(webhook_url: str, digest_markdown: str, timeout: float = 15.0) -> bool:
    """POST the digest to a Slack incoming webhook. Returns True iff Slack returns 200.
    Never raises — callers (e.g. the KPI harness) should be able to continue if Slack is down.
    """
    return post_message(webhook_url, digest_markdown, timeout=timeout)[0]
