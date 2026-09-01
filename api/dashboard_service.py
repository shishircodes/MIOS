"""The trends dashboard, counted from collected signals.

This replaces a page built entirely on invented numbers: a hardcoded twelve-week
series, fabricated sector totals, a literal `20` for the watchlist, and deltas
that read "↑ trending" without anything having been compared. It was the most
confident-looking screen in the product and the only one where nothing on it was
true. The invented series also ran an order of magnitude high — 847 Australian
roles a week against a real 73 — so anyone reading it formed a badly wrong idea
of what MIOS actually sees.

Two decisions shape what replaces it:

**A point per collection, not per calendar week.** The pipeline runs weekly, so
those usually coincide — but when a run is missed, a calendar series has to
decide what to draw for the gap, and every available answer lies. A zero says
nobody hired; interpolation invents a measurement; carrying the last value
forward repeats one. A series of collections says what it is: this is what we
found each time we looked.

**As many collections as exist, and no more.** The old page promised twelve
weeks and drew twelve regardless. This reports how many it actually has, so a
sparse history looks sparse.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)

#: How many collections the trend chart covers. Twelve weekly runs is a quarter,
#: which is long enough to show a season and short enough to stay legible.
TREND_COLLECTIONS = 12

#: Sector keys are stored as the classifier emits them. The reader should not
#: have to know that.
SECTOR_LABELS: dict[str, str] = {
    "mining": "Mining",
    "construction": "Construction",
    "oil_gas": "Oil & gas",
    "energy_transition": "Energy transition",
    "defence": "Defence",
    "logistics": "Logistics",
    "other": "Other",
}


def _pct_change(now: int, before: int) -> float | None:
    """Percentage movement, or None when there is nothing to compare against.

    None rather than zero: a first collection has no previous one, and reporting
    it as "no change" would claim a comparison that was never made. The old page
    printed "↑ trending" unconditionally, which is the same failure with more
    confidence.
    """
    if not before:
        return None
    return round((now - before) / before * 100, 1)


def build_dashboard_payload(target: str | Path | None = None) -> dict[str, Any]:
    """Everything the trends dashboard shows, counted from the signals table."""
    empty = {
        "collections": [], "latest": None, "change": {},
        "sectors": [], "watchlist": {"total": 0, "byTier": {}},
        "coverage": {"collections": 0, "from": None, "to": None},
        "trendWindow": TREND_COLLECTIONS,
    }

    try:
        with connect(target, readonly=True) as conn:
            # One row per collection day. Classified only: an unclassified row
            # has no sector or region yet, so counting it would move the totals
            # without being able to say where.
            rows = conn.execute(
                "SELECT substr(captured_at, 1, 10) AS day, count(*) AS total, "
                "sum(CASE WHEN region = 'AU' THEN 1 ELSE 0 END) AS au, "
                "sum(CASE WHEN region = 'PNG' THEN 1 ELSE 0 END) AS png "
                "FROM signals WHERE classified_at IS NOT NULL "
                "GROUP BY substr(captured_at, 1, 10) ORDER BY day"
            ).fetchall()

            latest_day = rows[-1]["day"] if rows else None
            sector_rows = []
            if latest_day:
                sector_rows = conn.execute(
                    "SELECT sector, count(*) AS n FROM signals "
                    "WHERE classified_at IS NOT NULL AND substr(captured_at, 1, 10) = ? "
                    "GROUP BY sector ORDER BY n DESC",
                    (latest_day,),
                ).fetchall()

            tiers = conn.execute(
                "SELECT tier, count(*) AS n FROM watchlist GROUP BY tier ORDER BY tier"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - an empty dashboard beats a broken one
        log.warning("dashboard: could not read (%s)", exc)
        return empty

    by_tier = {str(t["tier"]): int(t["n"] or 0) for t in tiers}
    watchlist = {"total": sum(by_tier.values()), "byTier": by_tier}

    if not rows:
        # No collections yet, but the watchlist is not a fact about collections.
        # Zeroing it here would report "0 watchlist companies" on a fresh
        # database that has twenty.
        return {**empty, "watchlist": watchlist}

    collections = [
        {"date": r["day"], "total": int(r["total"] or 0),
         "au": int(r["au"] or 0), "png": int(r["png"] or 0)}
        for r in rows
    ]
    recent = collections[-TREND_COLLECTIONS:]
    latest = recent[-1]
    previous = recent[-2] if len(recent) > 1 else None

    return {
        "collections": recent,
        "latest": latest,
        #: None where there is no previous collection to compare with, so the UI
        #: shows nothing rather than a delta it cannot justify.
        "change": {
            "total": _pct_change(latest["total"], previous["total"]) if previous else None,
            "au": _pct_change(latest["au"], previous["au"]) if previous else None,
            "png": _pct_change(latest["png"], previous["png"]) if previous else None,
        },
        "sectors": [
            {"key": str(s["sector"] or "other"),
             "label": SECTOR_LABELS.get(str(s["sector"] or "other"),
                                        str(s["sector"] or "other").replace("_", " ").title()),
             "count": int(s["n"] or 0)}
            for s in sector_rows
        ],
        "watchlist": watchlist,
        #: What the chart is actually standing on. The page it replaces claimed
        #: twelve weeks whatever it had.
        "coverage": {
            "collections": len(collections),
            "from": collections[0]["date"],
            "to": latest["date"],
        },
        "trendWindow": TREND_COLLECTIONS,
    }
