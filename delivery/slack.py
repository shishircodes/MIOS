"""Slack incoming-webhook delivery."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


def post_digest(webhook_url: str, digest_markdown: str, timeout: float = 15.0) -> bool:
    """POST the digest to a Slack incoming webhook. Returns True iff Slack returns 200.
    Never raises — callers (e.g. the KPI harness) should be able to continue if Slack is down.
    """
    if not webhook_url:
        log.error("post_digest: no webhook URL configured")
        return False
    try:
        resp = requests.post(
            webhook_url,
            json={"text": digest_markdown, "mrkdwn": True},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        log.error("post_digest: request failed: %s", exc)
        return False
    if resp.status_code == 200:
        log.info("post_digest: delivered (%d chars)", len(digest_markdown))
        return True
    log.error("post_digest: Slack returned %d: %s", resp.status_code, resp.text[:200])
    return False
