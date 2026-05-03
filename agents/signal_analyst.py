"""Signal Analyst agent. Classifies pending signals using Google Gemini."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rapidfuzz import fuzz, process

from agents.prompts import (
    BLOCKLIST_KEYWORDS,
    CLASSIFY_USER_PROMPT_TEMPLATE,
    MIN_CONTENT_LENGTH,
    SYSTEM_PROMPT,
)
from config.settings import settings

log = logging.getLogger(__name__)

ALLOWED_SECTORS = {"mining", "oil_gas", "construction", "defence", "energy_transition", "other"}
ALLOWED_CATEGORIES = {
    "hiring_velocity", "project", "leadership", "financial", "competitive", "market_intel"
}
ALLOWED_CYCLES = {"weekly", "monthly", "quarterly"}

FUZZY_THRESHOLD = 85

# Inter-call sleep to stay under Gemini free-tier per-minute rate limit
# (10 RPM on gemini-2.5-flash). 7s -> ~8.5 RPM steady-state.
INTER_CALL_DELAY_SECONDS = 7.0


# --------------------------------------------------------------------------
# Pre-filtering (no LLM call)
# --------------------------------------------------------------------------


def _is_blocklisted(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in BLOCKLIST_KEYWORDS)


def prefilter(raw_content: str) -> tuple[bool, str | None]:
    """Return (passes, drop_reason). passes=True means keep for LLM call."""
    if not raw_content or len(raw_content.strip()) < MIN_CONTENT_LENGTH:
        return False, "too_short"
    if _is_blocklisted(raw_content):
        return False, "blocklist"
    return True, None


# --------------------------------------------------------------------------
# Watchlist resolution
# --------------------------------------------------------------------------


def _load_watchlist(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT company_name, tier, sector, aliases FROM watchlist"
    ).fetchall()
    out = []
    for cname, tier, sector, aliases_json in rows:
        aliases = json.loads(aliases_json or "[]")
        out.append({
            "company_name": cname,
            "tier": tier,
            "sector": sector,
            "aliases": aliases,
            "all_names": [cname, *aliases],
        })
    return out


def fuzzy_match_watchlist(
    candidate: str | None,
    watchlist: list[dict[str, Any]],
    threshold: int = FUZZY_THRESHOLD,
) -> tuple[str | None, str | None]:
    """Fuzzy-match a candidate company name against the watchlist.
    Returns (matched_canonical_name, tier) or (None, None)."""
    if not candidate:
        return None, None
    candidate = candidate.strip()
    if not candidate:
        return None, None

    pool: list[tuple[str, dict[str, Any]]] = []
    for entry in watchlist:
        for name in entry["all_names"]:
            pool.append((name, entry))

    best = process.extractOne(
        candidate,
        [name for name, _ in pool],
        scorer=fuzz.WRatio,
        score_cutoff=threshold,
    )
    if not best:
        return None, None
    matched_name, score, idx = best
    entry = pool[idx][1]
    log.debug("fuzzy match: %r -> %r (score=%s, canonical=%s)",
              candidate, matched_name, score, entry["company_name"])
    return entry["company_name"], entry["tier"]


# --------------------------------------------------------------------------
# Gemini call
# --------------------------------------------------------------------------


RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "company_name", "sector", "signal_category", "review_cycle",
        "watchlist_match", "is_new_prospect", "reasoning",
    ],
    "properties": {
        "company_name": {"type": "string", "nullable": True},
        "sector": {
            "type": "string",
            "enum": ["mining", "oil_gas", "construction", "defence", "energy_transition", "other"],
        },
        "signal_category": {
            "type": "string",
            "enum": ["hiring_velocity", "project", "leadership", "financial", "competitive", "market_intel"],
        },
        "review_cycle": {"type": "string", "enum": ["weekly", "monthly", "quarterly"]},
        "watchlist_match": {"type": "string", "nullable": True},
        "is_new_prospect": {"type": "boolean"},
        "reasoning": {"type": "string"},
    },
}


def _build_gemini_caller() -> Callable[[str, str], dict[str, Any]]:
    """Return a function (system_prompt, user_prompt) -> parsed JSON dict."""
    from google import genai
    from google.genai import types

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")

    client = genai.Client(api_key=settings.gemini_api_key)
    model = settings.gemini_model

    def _call(system_prompt: str, user_prompt: str) -> dict[str, Any]:
        response = client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
                temperature=0.1,
            ),
        )
        return json.loads(response.text)

    return _call


def _call_with_retry(
    fn: Callable[[str, str], dict[str, Any]],
    system_prompt: str,
    user_prompt: str,
    max_retries: int = 5,
) -> dict[str, Any]:
    delay = 8.0
    for attempt in range(max_retries + 1):
        try:
            return fn(system_prompt, user_prompt)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            is_rate = "rate" in msg or "429" in msg or "quota" in msg or "resourceexhausted" in msg
            if is_rate and attempt < max_retries:
                log.warning("Gemini rate-limited (attempt %d/%d), backing off %.1fs",
                            attempt + 1, max_retries, delay)
                time.sleep(delay)
                delay *= 2
                continue
            raise


# --------------------------------------------------------------------------
# Validation + post-processing
# --------------------------------------------------------------------------


def _coerce_classification(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce any LLM-returned values into the allowed enum vocabulary.
    Falls back to safe defaults if the model returns something off-schema."""
    sector = payload.get("sector")
    if sector not in ALLOWED_SECTORS:
        sector = "other"
    category = payload.get("signal_category")
    if category not in ALLOWED_CATEGORIES:
        category = "hiring_velocity"
    cycle = payload.get("review_cycle")
    if cycle not in ALLOWED_CYCLES:
        cycle = "weekly"
    return {
        "company_name": payload.get("company_name"),
        "sector": sector,
        "signal_category": category,
        "review_cycle": cycle,
        "watchlist_match": payload.get("watchlist_match"),
        "is_new_prospect": bool(payload.get("is_new_prospect", False)),
        "reasoning": payload.get("reasoning") or "",
    }


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def classify_pending(
    db_path: str | Path,
    batch_size: int = 20,
    gemini_caller: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Classify unclassified signals.

    Returns counts keyed by signal_category, plus 'filtered_too_short',
    'filtered_blocklist', 'errors', 'classified', 'total_pending'.
    """
    counts: Counter[str] = Counter()
    if gemini_caller is None:
        gemini_caller = _build_gemini_caller()

    with sqlite3.connect(db_path) as conn:
        watchlist = _load_watchlist(conn)
        watchlist_canonicals = ", ".join(w["company_name"] for w in watchlist)

        rows = conn.execute(
            "SELECT signal_id, raw_content FROM signals "
            "WHERE classified_at IS NULL "
            "ORDER BY captured_at LIMIT ?",
            (batch_size,),
        ).fetchall()
        counts["total_pending"] = len(rows)
        log.info("classify_pending: %d pending signals", len(rows))

        for signal_id, raw in rows:
            ok, reason = prefilter(raw)
            if not ok:
                counts[f"filtered_{reason}"] += 1
                conn.execute(
                    "UPDATE signals SET classified_at = ?, "
                    "sector = COALESCE(sector, 'other'), "
                    "signal_category = COALESCE(signal_category, 'hiring_velocity'), "
                    "review_cycle = COALESCE(review_cycle, 'weekly'), "
                    "analysis_notes = ? "
                    "WHERE signal_id = ?",
                    (_now_iso(), f"prefiltered:{reason}", signal_id),
                )
                continue

            user_prompt = CLASSIFY_USER_PROMPT_TEMPLATE.format(
                watchlist_companies=watchlist_canonicals,
                raw_content=raw,
            )
            if counts.get("classified", 0) > 0:
                time.sleep(INTER_CALL_DELAY_SECONDS)
            try:
                payload = _call_with_retry(gemini_caller, SYSTEM_PROMPT, user_prompt)
            except Exception as exc:  # noqa: BLE001
                log.error("Gemini call failed for signal %s: %s", signal_id, exc)
                counts["errors"] += 1
                continue

            cls = _coerce_classification(payload)
            matched_name, matched_tier = fuzzy_match_watchlist(
                cls["watchlist_match"] or cls["company_name"], watchlist
            )
            if matched_name:
                cls["watchlist_match"] = matched_name
                cls["is_new_prospect"] = False
            else:
                cls["watchlist_match"] = None

            conn.execute(
                "UPDATE signals SET "
                "company_name = ?, sector = ?, signal_category = ?, review_cycle = ?, "
                "watchlist_tier = ?, is_new_prospect = ?, analysis_notes = ?, classified_at = ? "
                "WHERE signal_id = ?",
                (
                    cls["company_name"],
                    cls["sector"],
                    cls["signal_category"],
                    cls["review_cycle"],
                    matched_tier,
                    int(cls["is_new_prospect"]),
                    cls["reasoning"],
                    _now_iso(),
                    signal_id,
                ),
            )
            counts[cls["signal_category"]] += 1
            counts["classified"] += 1

        conn.commit()

    log.info("classify_pending done: %s", dict(counts))
    return dict(counts)
