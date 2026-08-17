"""Signal Analyst agent. Classifies pending signals using Google Gemini."""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rapidfuzz import fuzz

from agents.prompts import (
    BLOCKLIST_KEYWORDS,
    CLASSIFY_USER_PROMPT_TEMPLATE,
    MIN_CONTENT_LENGTH,
    SYSTEM_PROMPT,
)
from config.settings import settings
from loader.db import connect

log = logging.getLogger(__name__)

ALLOWED_SECTORS = {"mining", "oil_gas", "construction", "defence", "energy_transition", "other"}
ALLOWED_CATEGORIES = {
    "hiring_velocity", "project", "leadership", "financial", "competitive", "market_intel"
}
ALLOWED_CYCLES = {"weekly", "monthly", "quarterly"}

FUZZY_THRESHOLD = 85

# Free-tier hard limits (as of 2026-05 for gemini-2.5-flash / flash-lite)
DAILY_API_CALL_LIMIT = 20
# One batch = whole working set. Set high so a full 80-record run uses 1 quota slot.
# Gemini 2.5 Flash handles ~1M input tokens; the output cap is what limits us, so we
# also raise max_output_tokens on the GenerateContentConfig below.
SIGNALS_PER_API_CALL = 100
MAX_SIGNAL_CHARS = 3000           # truncate long raw_content to save tokens
MAX_OUTPUT_TOKENS = 32000         # ~150 tok/record × 100 records, with headroom

# Throttle: 6.5s between calls is plenty when you only get 20/day,
# but keeps you under the per-minute burst limit too.
MIN_SECONDS_BETWEEN_CALLS = 13

# --------------------------------------------------------------------------
# Throttling
# --------------------------------------------------------------------------

_last_call_time: float = 0.0


def _throttle() -> None:
    """Ensure at least MIN_SECONDS_BETWEEN_CALLS since the last API call."""
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < MIN_SECONDS_BETWEEN_CALLS:
        sleep_for = MIN_SECONDS_BETWEEN_CALLS - elapsed
        log.debug("Throttling: sleeping %.2fs", sleep_for)
        time.sleep(sleep_for)
    _last_call_time = time.time()


# --------------------------------------------------------------------------
# Daily quota tracking (persists across restarts)
# --------------------------------------------------------------------------


def _ensure_kv_store(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)")


def _daily_api_call_key() -> str:
    return f"gemini_api_calls_{datetime.now(timezone.utc).date().isoformat()}"


def _get_daily_api_calls(conn) -> int:
    row = conn.execute(
        "SELECT value FROM kv_store WHERE key = ?", (_daily_api_call_key(),)
    ).fetchone()
    return int(row[0]) if row and row[0] else 0


def _increment_daily_api_calls(conn) -> None:
    key = _daily_api_call_key()
    # The counter lives in a TEXT column, so the incremented integer has to be
    # cast back to TEXT. SQLite's loose typing forgives the mismatch; Postgres
    # rejects it outright ("column is of type text but expression is integer").
    # `kv_store.value` refers to the pre-existing row on both engines.
    conn.execute(
        """
        INSERT INTO kv_store(key, value) VALUES(?, '1')
        ON CONFLICT(key) DO UPDATE
        SET value = CAST(CAST(COALESCE(kv_store.value, '0') AS INTEGER) + 1 AS TEXT)
        """,
        (key,),
    )


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


def _load_watchlist(conn) -> list[dict[str, Any]]:
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


#: Legal-form and generic words that carry no identity. "Downer EDI Limited" and
#: "Downer" are the same client; "Ltd" must not contribute to a match.
_COMPANY_NOISE = {
    "pty", "ltd", "limited", "inc", "incorporated", "plc", "llc", "lp", "llp",
    "corp", "corporation", "co", "company", "group", "holdings", "holding",
    "international", "intl", "australia", "australian", "png", "nz", "global",
    "services", "service", "solutions", "resources", "industries", "enterprises",
    "sa", "nv", "bv", "ag", "gmbh", "as", "asa", "spa", "the", "and", "t/a", "ta",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise_company(name: str) -> str:
    """Lowercase, strip punctuation, and drop legal-form and geography words.

    'Downer EDI Limited' -> 'downer edi'; 'CAPS Australia Pty Ltd' -> 'caps'.
    """
    tokens = [t for t in _NON_ALNUM.sub(" ", (name or "").lower()).split() if t]
    kept = [t for t in tokens if t not in _COMPANY_NOISE]
    # Everything was noise ("Australia Pty Ltd") — fall back so it can still be
    # compared rather than matching every other emptied string.
    return " ".join(kept or tokens)


def _contains_phrase(haystack: str, needle: str) -> bool:
    """True when `needle` appears in `haystack` as whole words.

    Word boundaries matter: without them 'Vale' matches 'Valeria' and 'BHP'
    matches any string containing those letters in sequence.
    """
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def fuzzy_match_watchlist(
    candidate: str | None,
    watchlist: list[dict[str, Any]],
    threshold: int = FUZZY_THRESHOLD,
) -> tuple[str | None, str | None]:
    """Match a company name against the watchlist.

    Returns (canonical_name, tier) or (None, None).

    Deliberately NOT `fuzz.WRatio`, which was the previous implementation and
    produced false positives at scale — 26 of 29 tiered companies in production
    were wrong, including 'Hastings Deering PNG Ltd' -> ExxonMobil and
    'CAPS Australia Pty Ltd' -> Vale. WRatio blends in `partial_ratio`, which
    scores a short name highly against any longer string sharing a few
    characters, so short watchlist entries matched almost anything.

    A false positive here is expensive: Mode Monitor over-reports hiring
    velocity, and Mode Push tells the BD team to pitch a candidate to what is
    actually a competitor, labelled as an active client. The strategy below
    therefore prefers a miss over a wrong match:

      1. exact match once legal forms are stripped
      2. the watchlist name appears as whole words in the candidate
         ('Downer' in 'Downer EDI Limited')
      3. high token_sort_ratio on the normalised strings, for word-order
         differences and typos — no substring component
    """
    if not candidate or not candidate.strip():
        return None, None

    cand_norm = _normalise_company(candidate)
    if not cand_norm:
        return None, None

    best_entry: dict[str, Any] | None = None
    best_score = 0.0
    best_name = ""

    for entry in watchlist:
        for name in entry["all_names"]:
            name_norm = _normalise_company(name)
            if not name_norm:
                continue

            if cand_norm == name_norm:
                score = 100.0
            elif _contains_phrase(cand_norm, name_norm):
                # A contained match is strong, but rank longer names higher:
                # 'rio tinto' inside a string beats a bare 'bhp' appearing in it.
                score = 99.0 + len(name_norm) / 1000
            else:
                ratio = fuzz.token_sort_ratio(cand_norm, name_norm)
                if ratio < max(threshold, 90):
                    continue
                score = float(ratio)

            if score > best_score:
                best_entry, best_score, best_name = entry, score, name

    if best_entry is None:
        return None, None

    log.debug("watchlist match: %r -> %r (via %r, score=%.1f)",
              candidate, best_entry["company_name"], best_name, best_score)
    return best_entry["company_name"], best_entry["tier"]


# --------------------------------------------------------------------------
# Gemini call
# --------------------------------------------------------------------------


SINGLE_RESPONSE_SCHEMA: dict[str, Any] = {
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

BATCH_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": SINGLE_RESPONSE_SCHEMA,
}


def _build_gemini_caller() -> Callable[..., dict[str, Any]]:
    """Return a function (system_prompt, user_prompt, schema) -> parsed JSON."""
    from google import genai
    from google.genai import types

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")

    client = genai.Client(api_key=settings.gemini_api_key)
    model = settings.gemini_model

    def _call(
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=schema or SINGLE_RESPONSE_SCHEMA,
                temperature=0.1,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            ),
        )
        return json.loads(response.text)

    return _call


def _call_with_retry(
    fn: Callable[..., dict[str, Any]],
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any] | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Call Gemini with minimal retry logic.

    On rate-limit (429/quota), waits 60s once for the per-minute quota to reset,
    then gives up to avoid hammering the free tier.
    """
    for attempt in range(max_retries + 1):
        try:
            return fn(system_prompt, user_prompt, schema=schema)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            is_rate = "rate" in msg or "429" in msg or "quota" in msg or "resourceexhausted" in msg
            if is_rate and attempt < max_retries:
                log.warning(
                    "Gemini rate-limited (attempt %d/%d). Waiting 60s for quota reset...",
                    attempt + 1, max_retries,
                )
                time.sleep(60)
                continue
            raise


# --------------------------------------------------------------------------
# Batch prompt builder
# --------------------------------------------------------------------------

BATCH_USER_PROMPT_TEMPLATE = """You are a signal analyst. Classify each of the following {count} raw signals.

Allowed sectors: mining, oil_gas, construction, defence, energy_transition, other
Allowed categories: hiring_velocity, project, leadership, financial, competitive, market_intel
Allowed review cycles: weekly, monthly, quarterly

Watchlist companies: {watchlist_companies}

Return a JSON array containing exactly {count} classification objects, in the same order as the signals below. Each object must use this schema:
- company_name: string or null
- sector: one of the allowed sectors
- signal_category: one of the allowed categories
- review_cycle: one of weekly, monthly, quarterly
- watchlist_match: string or null (the matched watchlist company name, if any)
- is_new_prospect: boolean
- reasoning: string

Signals:
{signals_text}
"""


def _build_batch_prompt(
    chunk: list[tuple[Any, str]],
    watchlist_canonicals: str,
) -> str:
    parts: list[str] = []
    for idx, (_, raw) in enumerate(chunk, start=1):
        truncated = raw[:MAX_SIGNAL_CHARS] if len(raw) > MAX_SIGNAL_CHARS else raw
        parts.append(f"--- SIGNAL {idx} ---\n{truncated}")
    return BATCH_USER_PROMPT_TEMPLATE.format(
        count=len(chunk),
        watchlist_companies=watchlist_canonicals,
        signals_text="\n\n".join(parts),
    )


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
    batch_size: int | None = None,
    gemini_caller: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Classify unclassified signals.

    `batch_size` caps how many rows this run will touch. The default of `None`
    means "everything pending", leaving the daily Gemini quota as the only
    limit — which is the only ceiling that reflects a real constraint.

    Callers previously had to guess a number, and the guess went stale: a cap of
    100 was fine for two sources scraping 50 each, then a third source was added
    and every run silently left 50 rows unclassified. They were not lost — the
    next run picks them up — but the digest was always a week behind on a third
    of its input. An explicit `batch_size` is still honoured, for tests and for
    deliberately short runs.

    Returns counts keyed by signal_category, plus 'filtered_too_short',
    'filtered_blocklist', 'errors', 'classified', 'total_pending',
    'quota_exhausted', 'api_calls_made'.
    """
    counts: Counter[str] = Counter()
    if gemini_caller is None:
        gemini_caller = _build_gemini_caller()

    with connect(db_path) as conn:
        _ensure_kv_store(conn)

        # --- DAILY QUOTA CIRCUIT BREAKER ---
        daily_api_calls = _get_daily_api_calls(conn)
        remaining_calls = DAILY_API_CALL_LIMIT - daily_api_calls
        log.info("Daily quota: %d/%d API calls used, %d remaining",
                 daily_api_calls, DAILY_API_CALL_LIMIT, remaining_calls)
        if remaining_calls <= 0:
            log.warning("Daily Gemini quota exhausted. Skipping run.")
            counts["quota_exhausted"] = 1
            conn.commit()
            return dict(counts)

        # Cap signals to what quota allows, and to `batch_size` if one was given.
        max_signals = remaining_calls * SIGNALS_PER_API_CALL
        effective_limit = max_signals if batch_size is None else min(batch_size, max_signals)
        # ------------------------------------

        watchlist = _load_watchlist(conn)
        watchlist_canonicals = ", ".join(w["company_name"] for w in watchlist)

        # Fetch a buffer so we can prioritise watchlist mentions
        rows = conn.execute(
            "SELECT signal_id, raw_content FROM signals "
            "WHERE classified_at IS NULL "
            "ORDER BY captured_at LIMIT ?",
            (effective_limit * 2 + 50,),  # fetch extra for prioritisation
        ).fetchall()

        # Prioritise: signals mentioning watchlist companies get LLM time first
        def _priority_key(row: tuple[Any, str]) -> tuple[int, str]:
            signal_id, raw = row
            score = 0
            raw_lower = raw.lower()
            for entry in watchlist:
                for name in entry["all_names"]:
                    if name.lower() in raw_lower:
                        score += 10
                        break
            return (-score, str(signal_id))

        rows.sort(key=_priority_key)
        rows = rows[:effective_limit]

        counts["total_pending"] = len(rows)
        log.info("classify_pending: %d signals selected for processing", len(rows))

        # Group into chunks for batch API calls
        chunks = [
            rows[i:i + SIGNALS_PER_API_CALL]
            for i in range(0, len(rows), SIGNALS_PER_API_CALL)
        ]

        for chunk in chunks:
            if _get_daily_api_calls(conn) >= DAILY_API_CALL_LIMIT:
                log.warning("Daily API call limit reached mid-run. Stopping.")
                counts["quota_exhausted"] = 1
                break

            # Pre-filter each signal in the chunk individually
            pending_chunk: list[tuple[Any, str]] = []
            for signal_id, raw in chunk:
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
                else:
                    pending_chunk.append((signal_id, raw))

            if not pending_chunk:
                continue

            user_prompt = _build_batch_prompt(pending_chunk, watchlist_canonicals)

            _throttle()
            try:
                payload = _call_with_retry(
                    gemini_caller,
                    SYSTEM_PROMPT,
                    user_prompt,
                    schema=BATCH_RESPONSE_SCHEMA,
                )
            except Exception as exc:  # noqa: BLE001
                log.error("Gemini batch call failed for chunk of %d signals: %s",
                          len(pending_chunk), exc)
                counts["errors"] += len(pending_chunk)
                continue

            # Expect a list response
            if not isinstance(payload, list):
                log.error("Expected JSON array response, got %s", type(payload).__name__)
                counts["errors"] += len(pending_chunk)
                continue

            _increment_daily_api_calls(conn)
            counts["api_calls_made"] = counts.get("api_calls_made", 0) + 1

            for idx, (signal_id, raw) in enumerate(pending_chunk):
                if idx >= len(payload):
                    log.error("Batch response missing item %d for signal %s", idx, signal_id)
                    counts["errors"] += 1
                    continue

                cls = _coerce_classification(payload[idx])
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