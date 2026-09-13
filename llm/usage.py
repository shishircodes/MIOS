"""What the pipeline has spent on models, counted where it cannot be missed.

Written after a day the free Gemini tier ran out while the counter read zero.
Three things caused that, and all three are structural rather than accidental:

* The counter lived inside `classify_pending` and incremented only after a
  **successful** batch. Google charges the allowance for a rejected request the
  same as a served one, so every failure was invisible.
* Retries multiplied the invisibility. One failing batch with `max_retries=2`
  spends three of twenty requests and records none.
* Market Pulse built its own client and called Gemini directly, so a whole
  feature never touched the counter at all.

Counting therefore happens at the seam every call passes through
(`llm.providers.caller_for`) rather than at any call site, and it counts
attempts rather than successes.

Failures here never propagate. A model call that worked must not be reported as
failed because the bookkeeping could not be written — the run has already spent
the request either way.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)

#: Kept in `kv_store` rather than a table of its own. The rows are one integer
#: per provider per day; a table would be schema for a counter.
KEY_PREFIX = "llm_calls"


def _key(provider: str, when: date | None = None) -> str:
    return f"{KEY_PREFIX}:{provider}:{(when or datetime.now(timezone.utc).date()).isoformat()}"


def record(purpose: str, provider: str, model: str, *, ok: bool,
           note: str | None = None, target: str | Path | None = None) -> None:
    """Count one attempt. Never raises."""
    try:
        with connect(target) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute(
                "INSERT INTO kv_store(key, value) VALUES(?, '1') "
                "ON CONFLICT (key) DO UPDATE SET "
                "value = CAST(CAST(COALESCE(kv_store.value, '0') AS INTEGER) + 1 AS TEXT)",
                (_key(provider),),
            )
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not fail a run
        log.warning("llm.usage: could not record a %s call (%s)", provider, exc)
        return

    log.info("llm.usage: %s %s/%s %s%s", purpose, provider, model,
             "ok" if ok else "FAILED", f" ({note})" if note else "")


def used_today(provider: str, target: str | Path | None = None) -> int:
    """Attempts made against this provider today, successful or not."""
    try:
        with connect(target, readonly=True) as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = ?", (_key(provider),)).fetchone()
    except Exception as exc:  # noqa: BLE001
        log.warning("llm.usage: could not read the counter (%s)", exc)
        return 0
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def budget(target: str | Path | None = None) -> list[dict[str, Any]]:
    """Today's usage per provider against what it allows.

    `remaining` is what the admin screen needs to warn before a run rather than
    explain after one. It is advisory: the provider is the authority on its own
    limit, and a free tier can change without telling us.
    """
    from llm.providers import available_providers, _PROVIDERS

    out = []
    for p in available_providers():
        limit = getattr(_PROVIDERS[p["name"]], "free_tier_daily_requests", None)
        used = used_today(p["name"], target)
        out.append({
            "provider": p["name"],
            "label": p["label"],
            "configured": p["configured"],
            "usedToday": used,
            "dailyLimit": limit,
            "remaining": None if limit is None else max(0, limit - used),
        })
    return out


def history(days: int = 14, target: str | Path | None = None) -> list[dict[str, Any]]:
    """Recent daily totals per provider, newest first."""
    try:
        with connect(target, readonly=True) as conn:
            rows = conn.execute(
                "SELECT key, value FROM kv_store WHERE key LIKE ? ORDER BY key DESC",
                (f"{KEY_PREFIX}:%",),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("llm.usage: could not read history (%s)", exc)
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        parts = str(r["key"]).split(":")
        if len(parts) != 3:
            continue
        try:
            out.append({"provider": parts[1], "date": parts[2], "calls": int(r["value"])})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda e: e["date"], reverse=True)
    return out[: max(1, days) * max(1, len(set(e["provider"] for e in out)) or 1)]
