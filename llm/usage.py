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

Two records are kept, for two questions:

* **A daily counter per provider** in `kv_store` — "how much of today's
  allowance is left?", read before a run.
* **One row per call** in `llm_call_log`, with the tokens the provider reported
  — "what did this cost, and which job spent it?", across any time frame. A
  call count alone cannot answer that: one Market Pulse and one hundred-record
  classification are one call each and nothing alike in cost.

Failures here never propagate. A model call that worked must not be reported as
failed because the bookkeeping could not be written — the run has already spent
the request either way.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)

#: Kept in `kv_store` rather than a table of its own. The rows are one integer
#: per provider per day; a table would be schema for a counter.
KEY_PREFIX = "llm_calls"

#: Kept identical to the table in `loader/schema.sql`, and created here as well
#: so a database initialised before token tracking existed starts recording on
#: its next call.
_LOG_DDL = (
    "CREATE TABLE IF NOT EXISTS llm_call_log ("
    "id TEXT PRIMARY KEY, "
    "called_at TEXT NOT NULL, "
    "purpose TEXT, "
    "provider TEXT NOT NULL, "
    "model TEXT, "
    "ok INTEGER NOT NULL, "
    "input_tokens INTEGER, "
    "output_tokens INTEGER, "
    "cache_read_tokens INTEGER, "
    "cache_write_tokens INTEGER, "
    "note TEXT)"
)
_LOG_INDEX = "CREATE INDEX IF NOT EXISTS idx_llm_call_log_called_at ON llm_call_log (called_at)"

#: The time frames the admin screen offers: label, and days back (None = all).
RANGES: dict[str, tuple[str, int | None]] = {
    "today": ("Today", 1),
    "7d": ("Last 7 days", 7),
    "30d": ("Last 30 days", 30),
    "90d": ("Last 90 days", 90),
    "all": ("All time", None),
}


def _key(provider: str, when: date | None = None) -> str:
    return f"{KEY_PREFIX}:{provider}:{(when or datetime.now(timezone.utc).date()).isoformat()}"


def _int(value: Any) -> int | None:
    try:
        return None if value is None else max(0, int(value))
    except (TypeError, ValueError):
        return None


def record(purpose: str, provider: str, model: str, *, ok: bool,
           note: str | None = None, target: str | Path | None = None,
           input_tokens: int | None = None, output_tokens: int | None = None,
           cache_read_tokens: int | None = None,
           cache_write_tokens: int | None = None) -> None:
    """Count one attempt, with its tokens when the provider reported them.

    Never raises. Token arguments stay None for a failed call or a provider that
    reported nothing — "unknown" rather than zero, so an estimate never quietly
    under-counts.
    """
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

    try:
        with connect(target) as conn:
            conn.execute(_LOG_DDL)
            conn.execute(_LOG_INDEX)
            conn.execute(
                "INSERT INTO llm_call_log (id, called_at, purpose, provider, model, ok, "
                "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 purpose, provider, model, 1 if ok else 0,
                 _int(input_tokens), _int(output_tokens),
                 _int(cache_read_tokens), _int(cache_write_tokens), note),
            )
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not fail a run
        log.warning("llm.usage: could not log a %s call's tokens (%s)", provider, exc)

    tokens = (f" in={input_tokens} out={output_tokens}"
              if input_tokens is not None or output_tokens is not None else "")
    log.info("llm.usage: %s %s/%s %s%s%s", purpose, provider, model,
             "ok" if ok else "FAILED", f" ({note})" if note else "", tokens)


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


# --------------------------------------------------------------------------
# Tokens and estimated cost over a time frame
# --------------------------------------------------------------------------


def _empty_bucket() -> dict[str, Any]:
    return {"calls": 0, "failed": 0, "inputTokens": 0, "outputTokens": 0,
            "cacheReadTokens": 0, "cacheWriteTokens": 0, "costUsd": 0.0,
            #: Calls with tokens but no published rate — counted, not priced.
            "unpricedCalls": 0,
            #: Successful calls the provider reported no tokens for.
            "untrackedCalls": 0}


def _add(bucket: dict[str, Any], row: dict[str, Any], cost: float | None) -> None:
    bucket["calls"] += 1
    if not row["ok"]:
        bucket["failed"] += 1
        return
    if row["input_tokens"] is None and row["output_tokens"] is None:
        bucket["untrackedCalls"] += 1
        return
    bucket["inputTokens"] += row["input_tokens"] or 0
    bucket["outputTokens"] += row["output_tokens"] or 0
    bucket["cacheReadTokens"] += row["cache_read_tokens"] or 0
    bucket["cacheWriteTokens"] += row["cache_write_tokens"] or 0
    if cost is None:
        bucket["unpricedCalls"] += 1
    else:
        bucket["costUsd"] += cost


def report(range_key: str = "30d", *, now: datetime | None = None,
           target: str | Path | None = None) -> dict[str, Any]:
    """Tokens and estimated cost across a time frame.

    Days are UTC, the same days the daily counter uses, so "today" here and the
    call count beside it agree. Aggregated in Python rather than SQL: a week is
    a handful of rows, and cost depends on per-model rates the database does not
    hold.
    """
    from llm.pricing import PRICE_SOURCES, PRICES_AS_OF, estimate, rate_for, rate_table
    from llm.providers import _PROVIDERS
    from llm.purposes import PURPOSES

    if range_key not in RANGES:
        range_key = "30d"
    label, days = RANGES[range_key]
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone.utc).date()

    rows: list[dict[str, Any]] = []
    tracked_since: str | None = None
    try:
        with connect(target, readonly=True) as conn:
            first = conn.execute("SELECT min(called_at) AS first FROM llm_call_log").fetchone()
            tracked_since = first["first"] if first else None
            if days is None:
                since_day = (date.fromisoformat(tracked_since[:10]) if tracked_since else today)
            else:
                since_day = today - timedelta(days=days - 1)
            rows = [dict(r) for r in conn.execute(
                "SELECT called_at, purpose, provider, model, ok, input_tokens, output_tokens, "
                "cache_read_tokens, cache_write_tokens FROM llm_call_log "
                "WHERE called_at >= ? ORDER BY called_at",
                (since_day.isoformat(),),
            ).fetchall()]
    except Exception as exc:  # noqa: BLE001 - a missing table means nothing tracked yet
        log.debug("llm.usage: no call log to report from (%s)", exc)
        since_day = today if days is None else today - timedelta(days=days - 1)

    totals = _empty_bucket()
    by_model: dict[tuple[str, str], dict[str, Any]] = {}
    by_purpose: dict[str, dict[str, Any]] = {}
    daily: dict[str, dict[str, Any]] = {}

    for row in rows:
        cost = None
        if row["ok"] and (row["input_tokens"] is not None or row["output_tokens"] is not None):
            cost = estimate(row["provider"], row["model"] or "",
                            input_tokens=row["input_tokens"] or 0,
                            output_tokens=row["output_tokens"] or 0,
                            cache_read_tokens=row["cache_read_tokens"] or 0,
                            cache_write_tokens=row["cache_write_tokens"] or 0)
        day = str(row["called_at"])[:10]
        model_key = (str(row["provider"]), str(row["model"] or ""))
        purpose_key = str(row["purpose"] or "unknown")
        for bucket in (
            totals,
            by_model.setdefault(model_key, _empty_bucket()),
            by_purpose.setdefault(purpose_key, _empty_bucket()),
            daily.setdefault(day, _empty_bucket()),
        ):
            _add(bucket, row, cost)

    def _round(bucket: dict[str, Any]) -> dict[str, Any]:
        return {**bucket, "costUsd": round(bucket["costUsd"], 6)}

    series = []
    span = (today - since_day).days + 1
    for i in range(max(1, min(span, 366))):
        d = (since_day + timedelta(days=i)).isoformat()
        series.append({"date": d, **_round(daily.get(d, _empty_bucket()))})

    return {
        "range": range_key,
        "label": label,
        "ranges": [{"key": k, "label": v[0]} for k, v in RANGES.items()],
        "since": since_day.isoformat(),
        "until": today.isoformat(),
        #: When token tracking began. Calls before it were counted but carry no
        #: tokens, so a range reaching further back is a floor, not a total.
        "trackedSince": tracked_since,
        "pricesAsOf": PRICES_AS_OF,
        "priceSources": PRICE_SOURCES,
        "totals": _round(totals),
        "byModel": sorted(
            ({"provider": p, "providerLabel": getattr(_PROVIDERS.get(p), "label", p),
              "model": m, "priced": rate_for(p, m) is not None, **_round(b)}
             for (p, m), b in by_model.items()),
            key=lambda e: (-e["costUsd"], -e["calls"]),
        ),
        "byPurpose": sorted(
            ({"purpose": k, "label": getattr(PURPOSES.get(k), "label", k), **_round(b)}
             for k, b in by_purpose.items()),
            key=lambda e: (-e["costUsd"], -e["calls"]),
        ),
        "daily": series,
        "rates": rate_table(),
    }
