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

from loader.db import read_parallel
from scraper.publications import label_for as source_label, source_id_for

log = logging.getLogger(__name__)

#: How many collections the trend chart covers. Twelve weekly runs is a quarter,
#: which is long enough to show a season and short enough to stay legible.
TREND_COLLECTIONS = 12

#: The most the chart will draw however wide a window is asked for. A line with
#: sixty points across 700 pixels is a texture, not a trend.
MAX_TREND_COLLECTIONS = 26

#: Windows the interface offers. The last is "everything there is", which is
#: capped at MAX_TREND_COLLECTIONS like any other.
TREND_CHOICES = (4, 8, 12, MAX_TREND_COLLECTIONS)

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


def _sources(source_rows: list, legacy_news_urls: list) -> list[dict[str, Any]]:
    """Which sources produced the collection, by the name a reader knows.

    Counted per catalogue source. Rows stored under the old shared name
    "newsfeed" are split out by article address, so an older collection reads
    "Australian Mining 12, Mining.com.au 9" rather than "newsfeed 21" — the same
    names a newer collection reports under.
    """
    counts: dict[str, int] = {}
    kinds: dict[str, str] = {}
    for s in source_rows:
        name = str(s["source_name"] or "unknown")
        kinds[name] = str(s["source_type"] or "")
        if name == "newsfeed" and legacy_news_urls:
            continue  # counted below, one article at a time
        counts[name] = counts.get(name, 0) + int(s["n"] or 0)
    for r in legacy_news_urls:
        name = source_id_for("newsfeed", r["source_url"])
        counts[name] = counts.get(name, 0) + 1
        kinds.setdefault(name, kinds.get("newsfeed", "news"))

    total = sum(counts.values())
    return [
        {"name": name, "label": source_label(name), "kind": kinds.get(name, ""),
         "count": n, "share": _share(n, total)}
        for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


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


def build_dashboard_payload(
    target: str | Path | None = None,
    *,
    collection: str | None = None,
    region: str | None = None,
    trend: int | None = None,
) -> dict[str, Any]:
    """Everything the dashboard shows, counted from the signals table.

    `collection` selects which collection the composition panels describe, as a
    YYYY-MM-DD date. Defaults to the most recent. An unrecognised date falls
    back to the latest rather than returning nothing: a stale link should show
    the current week, not an empty page.

    `region` narrows every panel to one market. The trend chart still draws both
    series, because the question it answers — do these two move together — stops
    existing if one of them is filtered out.

    `trend` caps how many collections the chart covers, so a long history can be
    read a quarter at a time.
    """
    region = (region or "").strip().upper() or None
    if region not in (None, "AU", "PNG"):
        region = None
    window = max(2, min(int(trend or TREND_COLLECTIONS), MAX_TREND_COLLECTIONS))

    # Applied to every per-collection aggregate below. Kept as one pair so a
    # panel cannot quietly disagree with the others about what is being counted.
    where_region = " AND upper(coalesce(region, geography, '')) = ? " if region else " "
    # Outside the five sectors: left out of every figure, and counted on its own.
    RELEVANT = " AND COALESCE(sector, '') <> 'other' "
    args_region: tuple = (region,) if region else ()

    empty = {
        "collections": [], "latest": None, "change": {},
        "sectors": [], "watchlist": {"total": 0, "byTier": {}, "seen": 0, "seenShare": 0.0},
        "coverage": {"collections": 0, "from": None, "to": None},
        "trendWindow": TREND_COLLECTIONS,
        "categories": [], "groups": [], "sources": [], "companies": [],
        "newNames": 0, "run": None,
        "available": [], "selected": None, "region": region, "isLatest": True,
    }

    # Two batches of independent queries rather than ten in a row. Against the
    # hosted database each query is a network round trip, so a row of ten was
    # most of the page's load time; `read_parallel` runs a batch side by side.
    # The first batch finds the collection; the second describes it.
    def count(sql: str, params: tuple):
        return lambda conn: int((conn.execute(sql, params).fetchone() or {"n": 0})["n"] or 0)

    def fetch(sql: str, params: tuple = ()):
        return lambda conn: conn.execute(sql, params).fetchall()

    def latest_run(conn):
        # Read from the run log rather than inferred from the signals, so a run
        # that collected nothing still reports itself.
        try:
            return conn.execute(
                "SELECT id, trigger, status, started_at, finished_at, collected, note "
                "FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        except Exception as exc:  # noqa: BLE001 - the log may not exist yet
            log.debug("dashboard: no run log (%s)", exc)
            return None

    try:
        rows, tiers, run_row = read_parallel(target, [
            # One row per collection day. Classified only: an unclassified row
            # has no sector or region yet, so counting it would move the totals
            # without being able to say where.
            fetch(
                "SELECT substr(captured_at, 1, 10) AS day, count(*) AS total, "
                "sum(CASE WHEN region = 'AU' THEN 1 ELSE 0 END) AS au, "
                "sum(CASE WHEN region = 'PNG' THEN 1 ELSE 0 END) AS png "
                "FROM signals WHERE classified_at IS NOT NULL" + RELEVANT +
                "GROUP BY substr(captured_at, 1, 10) ORDER BY day"
            ),
            fetch("SELECT tier, count(*) AS n FROM watchlist GROUP BY tier ORDER BY tier"),
            latest_run,
        ])

        days = [str(r["day"]) for r in rows]
        # An unrecognised date falls back to the newest rather than showing
        # nothing: a stale link should land on the current week.
        latest_day = (collection if collection in days else (days[-1] if days else None))
        sector_rows: list = []
        category_rows: list = []
        source_rows: list = []
        legacy_news_urls: list = []
        company_rows: list = []
        new_names = 0
        seen_watchlist = 0
        not_relevant = 0
        if latest_day:
            day = (latest_day, *args_region)
            # Everything below describes the most recent collection. Kept as
            # separate aggregates rather than one wide query: they group by
            # different columns, and a single query would either repeat the
            # scan anyway or return a cross product to unpick in Python.
            (sector_rows, category_rows, source_rows, company_rows,
             new_names, seen_watchlist, not_relevant, legacy_news_urls) = read_parallel(target, [
                fetch(
                    "SELECT sector, count(*) AS n FROM signals "
                    "WHERE classified_at IS NOT NULL AND substr(captured_at, 1, 10) = ?"
                    + RELEVANT + where_region + "GROUP BY sector ORDER BY n DESC", day),
                fetch(
                    "SELECT signal_category, count(*) AS n FROM signals "
                    "WHERE classified_at IS NOT NULL AND substr(captured_at, 1, 10) = ?"
                    + RELEVANT + where_region + "GROUP BY signal_category ORDER BY n DESC", day),
                # Not filtered on classified_at: a source's contribution is what
                # it collected, and a row still awaiting classification was
                # still collected by it. Grouped by the collector alone, so one
                # collector with mixed source types is still one row.
                fetch(
                    "SELECT source_name, max(source_type) AS source_type, count(*) AS n "
                    "FROM signals WHERE substr(captured_at, 1, 10) = ?"
                    + where_region + "GROUP BY source_name ORDER BY n DESC, source_name", day),
                # 'Unknown' is what the classifier emits when it could not
                # identify the employer; nobody can act on it, so it is not
                # listed among the most active companies.
                fetch(
                    "SELECT company_name, count(*) AS n, "
                    "max(sector) AS sector, max(region) AS region, "
                    "max(watchlist_tier) AS tier, max(is_new_prospect) AS is_new "
                    "FROM signals WHERE classified_at IS NOT NULL "
                    "AND substr(captured_at, 1, 10) = ? "
                    "AND company_name IS NOT NULL AND lower(company_name) <> 'unknown'"
                    + RELEVANT + where_region
                    + "GROUP BY company_name ORDER BY n DESC, company_name LIMIT ?",
                    (*day, TOP_COMPANIES)),
                count(
                    "SELECT count(DISTINCT company_name) AS n FROM signals "
                    "WHERE is_new_prospect = 1 AND classified_at IS NOT NULL "
                    "AND substr(captured_at, 1, 10) = ?" + RELEVANT + where_region, day),
                # How much of the watchlist actually appeared: "we watch twenty
                # and saw seven this week" cannot be derived from tier counts.
                count(
                    "SELECT count(DISTINCT company_name) AS n FROM signals "
                    "WHERE watchlist_tier IS NOT NULL AND classified_at IS NOT NULL "
                    "AND substr(captured_at, 1, 10) = ?" + where_region, day),
                count(
                    "SELECT count(*) AS n FROM signals WHERE classified_at IS NOT NULL "
                    "AND COALESCE(sector, '') = 'other' "
                    "AND substr(captured_at, 1, 10) = ?" + where_region, day),
                # Articles stored under the old shared name "newsfeed", so they
                # can be counted towards the publication they came from. Empty
                # for any collection made since the publications were split.
                fetch(
                    "SELECT source_url FROM signals WHERE source_name = 'newsfeed' "
                    "AND substr(captured_at, 1, 10) = ?" + where_region, day),
            ])
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
    # The chart shows the tail; the panels describe the selected collection,
    # which is usually but not always the last of them.
    recent = collections[-window:]
    index = next((i for i, c in enumerate(collections) if c["date"] == latest_day),
                 len(collections) - 1)
    latest = collections[index]
    # Compared against the one before *the selected* collection, not before the
    # newest. Looking back at an earlier week should show the movement that was
    # reported at the time.
    previous = collections[index - 1] if index > 0 else None

    # Region narrows the headline too, or the tiles would disagree with the
    # panels underneath them.
    if region:
        picked = latest[region.lower()]
        prior = previous[region.lower()] if previous else None
        latest = {**latest, "total": picked}
        previous = {**previous, "total": prior} if previous else None

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
        "sources": _sources(source_rows, legacy_news_urls),
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
        #: Signals in the selected collection classified outside the five
        #: sectors, and so left out of every figure above.
        "notRelevant": not_relevant,
        "run": ({
            "id": str(run_row["id"]),
            "trigger": str(run_row["trigger"] or ""),
            "status": str(run_row["status"] or ""),
            "finishedAt": run_row["finished_at"],
            "collected": int(run_row["collected"] or 0),
            #: Set when the run finished but left part of its collection
            #: unclassified, which would otherwise read as a clean run.
            "note": run_row["note"],
        } if run_row is not None else None),
        "watchlist": watchlist,
        #: What the chart is actually standing on. The page it replaces claimed
        #: twelve weeks whatever it had.
        "coverage": {
            "collections": len(collections),
            "from": collections[0]["date"],
            "to": latest["date"],
        },
        "trendWindow": window,
        "trendChoices": list(TREND_CHOICES),
        #: Every collection that can be selected, newest first, with its size —
        #: so the picker can say what each one holds rather than listing bare
        #: dates.
        "available": [
            {"date": c["date"], "total": c["total"], "au": c["au"], "png": c["png"]}
            for c in reversed(collections)
        ],
        "selected": latest["date"],
        "region": region,
        #: Whether the panels describe the newest collection. The interface says
        #: so when they do not: a figure from three weeks ago presented without
        #: comment reads as current.
        "isLatest": bool(collections and latest["date"] == collections[-1]["date"]),
    }
