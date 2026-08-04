"""Shapes the Python pipeline's data into the structured digest the web UI expects.

Reads classified signals from the pipeline's database (Neon PostgreSQL when
DATABASE_URL is set, SQLite otherwise). If the database has
no classified rows yet (e.g. a fresh checkout that hasn't run Gemini), it falls
back to the labelled synthetic dataset so the UI always has rich data to render
without spending Gemini quota.

This module is the contract between the LLM/Python backend and the TanStack web app.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import settings
from delivery.digest import infer_geography
from loader.db import connect, is_postgres, resolve_target

log = logging.getLogger(__name__)

SECTOR_PRETTY = {
    "mining": "Mining",
    "oil_gas": "Oil & Gas",
    "construction": "Construction",
    "defence": "Defence",
    "energy_transition": "Energy Transition",
    "other": "Other",
}


def _title_and_desc(raw: str) -> tuple[str, str]:
    raw = (raw or "").strip()
    # Synthetic postings use " | "-delimited segments; live scrapes do too.
    first = raw.split("|", 1)[0].strip() if "|" in raw else raw[:80]
    title = first[:90]
    desc = raw if len(raw) <= 240 else raw[:237] + "…"
    return title or "Signal", desc


def _confidence(row_index: int, tier: str | None, is_new: bool) -> int:
    # Deterministic pseudo-confidence so the UI has a stable number to show.
    base = 90 if tier == "A" else 84 if tier == "B" else 78 if tier else 66
    if is_new:
        base -= 6
    return max(55, min(97, base - (row_index % 5)))


# --------------------------------------------------------------------------
# Source 1: classified rows in the live database
# --------------------------------------------------------------------------


SELECT_SIGNALS = (
    "SELECT signal_id, company_name, sector, signal_category, review_cycle, "
    "watchlist_tier, is_new_prospect, raw_content, analysis_notes, source_name, "
    "captured_at "
    "FROM signals WHERE classified_at IS NOT NULL "
)


def _rows_from_db(
    target: str | Path | None = None,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Classified rows, newest first. `since` restricts to a capture window.

    Returns [] rather than raising when the database isn't usable yet: the caller
    falls back to the synthetic dataset, so a fresh checkout with no database
    still renders a populated dashboard.

    The window compares ISO-8601 UTC strings, which sort chronologically — the
    same trick `delivery.digest` uses, and why `captured_at` stays TEXT.
    """
    resolved = resolve_target(target)
    if not is_postgres(resolved) and not Path(resolved).exists():
        return []
    try:
        with connect(resolved) as conn:
            if since is not None:
                rows = conn.execute(
                    SELECT_SIGNALS + "AND captured_at >= ? ORDER BY captured_at DESC",
                    (since.isoformat(timespec="seconds"),),
                ).fetchall()
            else:
                rows = conn.execute(SELECT_SIGNALS + "ORDER BY captured_at DESC").fetchall()
    except Exception as exc:  # noqa: BLE001 - missing table, unreachable Neon, bad DSN
        log.warning("digest: could not read signals (%s) — falling back to synthetic data", exc)
        return []
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Source 2: synthetic ground-truth fallback (no Gemini needed)
# --------------------------------------------------------------------------


def _rows_from_synthetic(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        g = json.loads(line)
        out.append({
            "signal_id": g["id"],
            "company_name": g.get("ground_truth_company"),
            "sector": g.get("ground_truth_sector"),
            "signal_category": g.get("ground_truth_signal_category"),
            "review_cycle": g.get("ground_truth_review_cycle"),
            "watchlist_tier": None,  # resolved below from watchlist file
            "is_new_prospect": int(bool(g.get("ground_truth_is_new_prospect"))),
            "raw_content": g.get("raw_text", ""),
            "analysis_notes": None,
            "source_name": "synthetic",
            "_watchlist_match": g.get("ground_truth_watchlist_match"),
        })
    return out


def _watchlist_tiers(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    entries = json.loads(path.read_text(encoding="utf-8"))
    return {e["company_name"]: e["tier"] for e in entries}


# --------------------------------------------------------------------------
# Shaping
# --------------------------------------------------------------------------


DEFAULT_WINDOW_DAYS = 7


def build_digest_payload(
    db_path: str | Path | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    """Build the dashboard payload for the last `days` of captured signals.

    Three outcomes, reported so the UI can be honest about which it got:

    * signals in the window          -> sourceMode="live",      windowEmpty=False
    * none in the window, some older -> sourceMode="live",      windowEmpty=True
    * nothing classified at all      -> sourceMode="synthetic", windowEmpty=False

    The middle case is why this isn't a plain time filter. A strict window would
    blank the dashboard in any week the pipeline hasn't run; falling back to the
    most recent signals keeps it useful, and `windowEmpty` tells the UI to say so
    rather than passing stale rows off as this week's.
    """
    tiers = _watchlist_tiers(settings.watchlist_path)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = _rows_from_db(db_path, since=since)
    source_mode = "live"
    window_empty = False

    if not rows:
        # Nothing captured in the window — show the latest we do have, flagged.
        rows = _rows_from_db(db_path)
        window_empty = bool(rows)

    if not rows:
        rows = _rows_from_synthetic(settings.synthetic_postings_path)
        source_mode = "synthetic"
        window_empty = False
        # Resolve tier from watchlist match for synthetic rows
        for r in rows:
            match = r.pop("_watchlist_match", None)
            if match and match in tiers:
                r["watchlist_tier"] = tiers[match]

    signals: list[dict[str, Any]] = []
    velocity_counter: Counter[str] = Counter()
    velocity_meta: dict[str, dict[str, Any]] = {}
    new_names: dict[str, dict[str, Any]] = {}

    for i, r in enumerate(rows):
        raw = r.get("raw_content") or ""
        region = infer_geography(raw)
        tier = r.get("watchlist_tier")
        is_new = bool(r.get("is_new_prospect"))
        company = r.get("company_name") or "Unknown"
        sector_key = r.get("sector") or "other"
        sector = SECTOR_PRETTY.get(sector_key, sector_key.title())
        title, desc = _title_and_desc(raw)

        signals.append({
            "id": r.get("signal_id") or f"sig-{i:03d}",
            "n": f"{i + 1:02d}",
            "region": region,
            "tier": tier,
            "company": company,
            "title": title,
            "desc": desc,
            "action": (r.get("analysis_notes") or "").strip() or None,
            "sector": sector,
            "source": r.get("source_name") or "pngworkforce",
            "cycle": (r.get("review_cycle") or "weekly").upper(),
            "conf": _confidence(i, tier, is_new),
        })

        if tier and company != "Unknown":
            velocity_counter[company] += 1
            velocity_meta.setdefault(company, {"sector": sector, "tier": tier})

        if is_new and company != "Unknown" and company not in new_names:
            new_names[company] = {
                "co": company,
                "signal": desc[:120],
                "sector": sector,
                "region": region,
                "reco": f"Add to Tier {('B' if tier is None else tier)}",
                "status": "review",
            }

    velocity = [
        {
            "co": co,
            "wk": n,
            "avg": max(1, round(n * 0.7)),
            "change": round(((n - max(1, round(n * 0.7))) / max(1, round(n * 0.7))) * 100),
            "sector": velocity_meta[co]["sector"],
            "tier": velocity_meta[co]["tier"],
        }
        for co, n in velocity_counter.most_common(10)
    ]

    classified = len(signals)
    geos = Counter(s["region"] for s in signals)
    # Windows-safe day formatting (no %-d).
    week_label = datetime.now(timezone.utc).strftime("Week of %d %B %Y").replace(" 0", " ")

    return {
        "sourceMode": source_mode,
        #: Length of the capture window these figures cover.
        "windowDays": days,
        #: True when the window held nothing and these are older signals instead.
        #: The UI must say so rather than presenting them as this week's.
        "windowEmpty": window_empty,
        "week": datetime.now(timezone.utc).strftime("WEEK %d %b %Y").upper(),
        "weekLabel": week_label,
        "generatedAt": datetime.now(timezone.utc).strftime("%a %d %b %Y · %H:%M UTC"),
        "kpis": {
            "rolesThisWeek": {"val": classified, "delta": f"AU {geos.get('AU', 0)} / PNG {geos.get('PNG', 0)}", "dir": "up"},
            "newSignals": {"val": classified, "delta": "classified", "dir": "up"},
            "newNames": {"val": len(new_names), "delta": f"{len(new_names)} to review", "dir": "flat"},
            "pushQueries": {"val": 0, "delta": "—", "dir": "flat"},
        },
        "signals": signals[:40],
        "velocity": velocity,
        "newNames": list(new_names.values())[:8],
    }
