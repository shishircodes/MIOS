"""Where the weekly digest goes in Slack, set from Admin › Integrations.

An administrator points the digest at a channel — or stops it while a channel
is reorganised — from the panel, checks it with a test message, and sees how
the last delivery went. None of that needs a deployment.

Same rules as every other key in the panel:

* **The webhook is a secret.** Anyone holding the URL can post into the
  channel, so it is stored encrypted with MIOS_CREDENTIAL_KEY (see
  `loader.credentials`) and never returned — only its last four characters.
* **The panel is the only place it is set.** There is no environment
  fallback: with no webhook stored, nothing is posted and the run carries on.
* **Switching it off does not forget the webhook.** The digest is still built
  and archived every run; only the post is skipped, and the history says so.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)

KEY_NAME = "slack_webhook"
SETTINGS_KEY = "slack:digest"
LAST_KEY = "slack:last_delivery"

#: Incoming webhooks only. Workflow Builder triggers (hooks.slack.com/triggers/…)
#: expect named variables rather than a message, and would accept the post and
#: show nothing.
WEBHOOK_PREFIX = "https://hooks.slack.com/services/"

TEST_MESSAGE = ("*MIOS test message* — the weekly digest will be posted in this channel "
                "after each pipeline run.")


class SlackConfigError(ValueError):
    """Bad input from the panel. The message is shown to the administrator."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _kv_get(key: str, target) -> Any:
    try:
        with connect(target, readonly=True) as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.debug("slack_config: could not read %s (%s)", key, exc)
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


def webhook(target=None) -> str:
    """The webhook entered in the panel, or "" when there is none."""
    from loader.credentials import key_for

    return key_for(KEY_NAME, target)


def enabled(target=None) -> bool:
    """Whether runs post the digest. On unless an administrator switched it off."""
    stored = _kv_get(SETTINGS_KEY, target) or {}
    return stored.get("enabled", True) is not False


def last_delivery(target=None) -> dict[str, Any] | None:
    return _kv_get(LAST_KEY, target)


def _record(kind: str, ok: bool, detail: str, by: str | None, target) -> dict[str, Any]:
    entry = {"at": _now(), "kind": kind, "ok": ok, "detail": detail, "by": by}
    try:
        _kv_set(LAST_KEY, entry, target)
    except Exception as exc:  # noqa: BLE001 - never fail a run over bookkeeping
        log.warning("slack_config: could not record the delivery (%s)", exc)
    return entry


# --------------------------------------------------------------------------
# Changing it
# --------------------------------------------------------------------------


def set_webhook(url: str, *, changed_by: str, target=None) -> None:
    from loader.credentials import set_key

    url = (url or "").strip()
    if not url.startswith(WEBHOOK_PREFIX):
        raise SlackConfigError(
            f"That is not a Slack incoming-webhook URL. It should start with {WEBHOOK_PREFIX} — "
            "create one under your Slack app's Incoming Webhooks.")
    set_key(KEY_NAME, url, changed_by=changed_by, target=target)


def clear_webhook(target=None) -> bool:
    from loader.credentials import clear_key

    return clear_key(KEY_NAME, target)


def set_enabled(on: bool, *, changed_by: str, target=None) -> None:
    _kv_set(SETTINGS_KEY, {"enabled": bool(on), "changedBy": changed_by, "changedAt": _now()},
            target)
    log.info("slack_config: %s turned the Slack digest %s", changed_by, "on" if on else "off")


# --------------------------------------------------------------------------
# Posting
# --------------------------------------------------------------------------


def deliver_digest(text: str, *, target=None) -> bool:
    """Post a run's digest, if Slack is set up and switched on. Returns delivered."""
    from delivery.slack import post_message

    if not enabled(target):
        log.info("live: Slack digest switched off in Admin — not posted")
        _record("digest", False, "switched off in Admin — not posted", None, target)
        return False
    url = webhook(target)
    if not url:
        log.warning("live: no Slack webhook configured — skipping Slack delivery")
        return False
    ok, detail = post_message(url, text)
    _record("digest", ok, detail, None, target)
    return ok


def send_test(*, by: str, target=None) -> dict[str, Any]:
    """Post a short test message, whether or not the digest is switched on."""
    from delivery.slack import post_message

    url = webhook(target)
    if not url:
        raise SlackConfigError("There is no webhook to test. Add one first.")
    ok, detail = post_message(url, TEST_MESSAGE)
    return _record("test", ok, detail, by, target)


def status(target=None) -> dict[str, Any]:
    """What the panel shows. Never the webhook itself."""
    from loader.credentials import available, describe

    key = describe(KEY_NAME, target=target)
    stored = _kv_get(SETTINGS_KEY, target) or {}
    return {
        "webhook": {k: key.get(k) for k in ("source", "hint", "unreadable")},
        "canStoreKey": available(),
        "enabled": enabled(target),
        "changedBy": stored.get("changedBy"),
        "changedAt": stored.get("changedAt"),
        "lastDelivery": last_delivery(target),
    }


__all__ = [
    "SlackConfigError", "clear_webhook", "deliver_digest", "enabled", "send_test",
    "set_enabled", "set_webhook", "status", "webhook",
]
