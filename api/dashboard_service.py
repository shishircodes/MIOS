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


#: What each kind of signal says about whether now is a moment to act.
#:
#: This is a judgement, not a measurement, and the interface says so. A new
#: project or a leadership change is a decision point — budgets move and teams
#: get built. Routine vacancies say a company is ticking over. Competitive and
#: market intelligence describe the market rather than the company.
#:
#: The same three-way split the Mode Push scorer weights by, kept deliberately
#: coarse here: the dashboard is answering "what kind of week was this", not
#: scoring anything.
CATEGORY_GROUP: dict[str, str] = {
    "project": "acting",
    "leadership": "acting",
    "financial": "acting",
    "hiring_velocity": "routine",
    "market_intel": "context",
    "competitive": "context",
}
GROUP_LABEL: dict[str, str] = {
    "acting": "Decision points",
    "routine": "Routine hiring",
    "context": "Market context",
}
GROUP_WHAT: dict[str, str] = {
    "acting": "A project, a leadership change or a financial event — something "
              "moved, and there is a reason to call this week rather than next.",
    "routine": "Ordinary vacancies. A company ticking over, which is worth "
               "knowing and is not by itself a reason to make contact.",
    "context": "Market and competitor intelligence. Background for a "
               "conversation rather than a reason to start one.",
}

CATEGORY_LABELS: dict[str, str] = {
    "project": "Project",
    "leadership": "Leadership",
    "financial": "Financial",
    "hiring_velocity": "Hiring velocity",
    "market_intel": "Market intel",
    "competitive": "Competitive",
}

#: How many companies the "most active" table lists. Enough to see a shape,
#: short enough to read without scrolling.
TOP_COMPANIES = 8


def _label_for(key: str, table: dict[str, str]) -> str:
    return table.get(key, key.replace("_", " ").capitalize())


def _share(count: int, total: int) -> float:
    return round(count / total * 100, 1) if total else 0.0


def _groups(category_rows: list, total: int) -> list[dict[str, Any]]:
    """The three-way split, in a fixed order.

    Fixed rather than sorted by size, because the order carries meaning: these
    run from "a reason to act" to "background", and re-ordering them week to
    week would make the bar harder to read across weeks, not easier.

    A group with nothing in it is still returned, at zero. Dropping it would
    make a week with no decision points look like a week where the question was
    not asked.
    """
    counts: dict[str, int] = {"acting": 0, "routine": 0, "context": 0}
    for row in category_rows:
        key = str(row["signal_category"] or "")
        counts[CATEGORY_GROUP.get(key, "context")] += int(row["n"] or 0)
    return [
        {"key": g, "label": GROUP_LABEL[g], "what": GROUP_WHAT[g],
         "count": counts[g], "share": _share(counts[g], total)}
        for g in ("acting", "routine", "context")
    ]


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
        "sectors": [], "watchlist": {"total": 0, "byTier": {}, "seen": 0, "seenShare": 0.0},
        "coverage": {"collections": 0, "from": None, "to": None},
        "trendWindow": TREND_COLLECTIONS,
        "categories": [], "groups": [], "sources": [], "companies": [],
        "newNames": 0, "run": None,
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
            sector_rows: list = []
            category_rows: list = []
            source_rows: list = []
            company_rows: list = []
            new_names = 0
            seen_watchlist = 0
            if latest_day:
                # Everything below describes the most recent collection. Kept as
                # separate aggregates rather than one wide query: they group by
                # different columns, and a single query would either repeat the
                # scan anyway or return a cross product to unpick in Python.
                sector_rows = conn.execute(
                    "SELECT sector, count(*) AS n FROM signals "
                    "WHERE classified_at IS NOT NULL AND substr(captured_at, 1, 10) = ? "
                    "GROUP BY sector ORDER BY n DESC",
                    (latest_day,),
                ).fetchall()
                category_rows = conn.execute(
                    "SELECT signal_category, count(*) AS n FROM signals "
                    "WHERE classified_at IS NOT NULL AND substr(captured_at, 1, 10) = ? "
                    "GROUP BY signal_category ORDER BY n DESC",
                    (latest_day,),
                ).fetchall()
                # Not filtered on classified_at: a source's contribution is what
                # it collected, and a row still awaiting classification was
                # still collected by it.
                # Grouped by the collector alone. Including source_type in the
                # GROUP BY split one collector into two rows whenever its
                # signals carried different types — the panel listed "adzuna"
                # twice, which reads as two sources rather than one.
                source_rows = conn.execute(
                    "SELECT source_name, max(source_type) AS source_type, count(*) AS n "
                    "FROM signals WHERE substr(captured_at, 1, 10) = ? "
                    "GROUP BY source_name ORDER BY n DESC, source_name",
                    (latest_day,),
                ).fetchall()
                company_rows = conn.execute(
                    "SELECT company_name, count(*) AS n, "
                    "max(sector) AS sector, max(region) AS region, "
                    "max(watchlist_tier) AS tier, max(is_new_prospect) AS is_new "
                    "FROM signals WHERE classified_at IS NOT NULL "
                    "AND substr(captured_at, 1, 10) = ? "
                    # 'Unknown' is what the classifier emits when it could not
                    # identify the employer. Those rows cannot be approached, so
                    # listing them among the most active companies would be
                    # offering a name nobody can act on.
                    "AND company_name IS NOT NULL AND lower(company_name) <> 'unknown' "
                    "GROUP BY company_name ORDER BY n DESC, company_name LIMIT ?",
                    (latest_day, TOP_COMPANIES),
                ).fetchall()
                new_names = int((conn.execute(
                    "SELECT count(DISTINCT company_name) AS n FROM signals "
                    "WHERE is_new_prospect = 1 AND classified_at IS NOT NULL "
                    "AND substr(captured_at, 1, 10) = ?", (latest_day,)
                ).fetchone() or {"n": 0})["n"] or 0)
                # How much of the watchlist actually appeared. "We watch twenty
                # and saw seven this week" is the question the tile answers, and
                # it cannot be derived from the tier counts alone.
                seen_watchlist = int((conn.execute(
                    "SELECT count(DISTINCT company_name) AS n FROM signals "
                    "WHERE watchlist_tier IS NOT NULL AND classified_at IS NOT NULL "
                    "AND substr(captured_at, 1, 10) = ?", (latest_day,)
                ).fetchone() or {"n": 0})["n"] or 0)

            tiers = conn.execute(
                "SELECT tier, count(*) AS n FROM watchlist GROUP BY tier ORDER BY tier"
            ).fetchall()

            # The run behind the latest collection, for the header. Read from
            # the run log rather than inferred from the signals, so a run that
            # collected nothing still reports itself.
            run_row = None
            try:
                run_row = conn.execute(
                    "SELECT id, trigger, status, started_at, finished_at, collected "
                    "FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
            except Exception as exc:  # noqa: BLE001 - the log may not exist yet
                log.debug("dashboard: no run log (%s)", exc)
    except Exception as exc:  # noqa: BLE001 - an empty dashboard beats a broken one
        log.warning("dashboard: could not read (%s)", exc)
        return empty

    by_tier = {str(t["tier"]): int(t["n"] or 0) for t in tiers}
    watchlist = {
        "total": sum(by_tier.values()),
        "byTier": by_tier,
        #: How many of them turned up in the latest collection. A watchlist is
        #: only worth keeping if the pipeline is actually seeing the companies
        #: on it, and that is not visible from the tier counts.
        "seen": seen_watchlist,
        "seenShare": _share(seen_watchlist, sum(by_tier.values())),
    }

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
             "count": int(s["n"] or 0),
             "share": _share(int(s["n"] or 0), latest["total"])}
            for s in sector_rows
        ],
        #: What kind of signals this collection was made of, and the coarse
        #: three-way grouping over them. Both are returned: the groups answer
        #: "what kind of week was this" at a glance, the categories are the
        #: detail behind that, and neither is derivable from the other in the
        #: browser without duplicating the mapping.
        "categories": [
            {"key": str(c["signal_category"] or "other"),
             "label": _label_for(str(c["signal_category"] or "other"), CATEGORY_LABELS),
             "group": CATEGORY_GROUP.get(str(c["signal_category"] or ""), "context"),
             "count": int(c["n"] or 0),
             "share": _share(int(c["n"] or 0), latest["total"])}
            for c in category_rows
        ],
        "groups": _groups(category_rows, latest["total"]),
        #: Which collectors produced this week's signals. A source missing from
        #: this list contributed nothing, which is the fastest way to see a
        #: scraper that has quietly stopped working.
        "sources": [
            {"name": str(s["source_name"] or "unknown"),
             "kind": str(s["source_type"] or ""),
             "count": int(s["n"] or 0),
             "share": _share(int(s["n"] or 0), sum(int(x["n"] or 0) for x in source_rows))}
            for s in source_rows
        ],
        "companies": [
            {"name": str(c["company_name"]),
             "count": int(c["n"] or 0),
             "sector": SECTOR_LABELS.get(str(c["sector"] or ""), str(c["sector"] or "—")),
             "region": str(c["region"] or "—"),
             "tier": (str(c["tier"]) if c["tier"] else None),
             "isNew": bool(c["is_new"])}
            for c in company_rows
        ],
        #: Companies seen this collection that are not on the watchlist. The
        #: pipeline's other job besides watching known names is finding new ones.
        "newNames": new_names,
        "run": ({
            "id": str(run_row["id"]),
            "trigger": str(run_row["trigger"] or ""),
            "status": str(run_row["status"] or ""),
            "finishedAt": run_row["finished_at"],
            "collected": int(run_row["collected"] or 0),
        } if run_row is not None else None),
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
