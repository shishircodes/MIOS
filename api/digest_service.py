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
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import settings
from delivery.digest import infer_geography, interleave, interleave_regions, rank_signal
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
    # `geography` is what the scraper recorded about its own market. Without it
    # the region falls back to keyword inference alone, which filed PNGworkforce
    # jobs under Australia whenever their teaser named no PNG landmark.
    "geography, captured_at "
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

#: How many signals the dashboard renders. Selection is balanced across regions,
#: so this cap can never silently drop a whole geography.
MAX_SIGNALS_SHOWN = 40


def _fmt_day(d: datetime) -> str:
    # Windows-safe: %-d is not supported by the MSVC strftime.
    return d.strftime("%d %B %Y").lstrip("0")


#: How many earlier windows the hiring-velocity baseline averages over. Four
#: weeks is what a consultant intuitively means by "the recent norm", and it is
#: short enough that a genuine ramp-up still moves the number.
BASELINE_WINDOWS = 4


def _parse_ts(raw: Any) -> datetime | None:
    """`captured_at` as an aware UTC datetime, or None if unparseable.

    Timestamps written by the scrapers are ISO-8601 with an offset, but rows
    imported from the old SQLite database can be naive. Those are read as UTC —
    that is what they were — so the two never compare as hours apart.
    """
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _day_after(ts: datetime) -> datetime:
    """Midnight UTC following `ts` — the exclusive end of the day it falls in."""
    return datetime.combine(ts.date(), datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)


def _collection_span(rows: list[dict[str, Any]]) -> tuple[datetime | None, datetime | None]:
    """Earliest and latest `captured_at` across the rendered rows.

    Synthetic rows carry no capture time, so this returns (None, None) for them.
    """
    stamps = [ts for r in rows if (ts := _parse_ts(r.get("captured_at"))) is not None]
    if not stamps:
        return None, None
    return min(stamps), max(stamps)


def _history_windows(
    target: str | Path | None,
    *,
    window_start: datetime,
    days: int,
    windows: int = BASELINE_WINDOWS,
) -> list[Counter[str]]:
    """Per-company watchlist signal counts for each period before `window_start`.

    Returns one Counter per period, oldest first, covering only the periods in
    which the pipeline actually ran. That exclusion is the whole point: a week
    nobody scraped is not a week in which nobody hired, and counting it as zero
    would drag every baseline down and manufacture a rise that never happened.

    Returns [] when there is no prior history at all, which the caller reports
    as "no baseline" rather than inventing one.
    """
    resolved = resolve_target(target)
    if not is_postgres(resolved) and not Path(resolved).exists():
        return []

    oldest = window_start - timedelta(days=days * windows)
    try:
        with connect(resolved) as conn:
            rows = conn.execute(
                "SELECT company_name, watchlist_tier, captured_at FROM signals "
                "WHERE classified_at IS NOT NULL "
                "AND captured_at >= ? AND captured_at < ?",
                (
                    oldest.isoformat(timespec="seconds"),
                    window_start.isoformat(timespec="seconds"),
                ),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - same failure modes as _rows_from_db
        log.warning("digest: no velocity baseline available (%s)", exc)
        return []

    buckets: list[Counter[str]] = [Counter() for _ in range(windows)]
    ran = [False] * windows

    for r in rows:
        ts = _parse_ts(r["captured_at"])
        if ts is None:
            continue
        # Bucket 0 is the oldest period; the one just before `window_start` is last.
        age = (window_start - ts).total_seconds() / 86400.0
        idx = windows - 1 - int(age // days)
        if not 0 <= idx < windows:
            continue
        # A run happened in this period regardless of who was hiring, so the
        # period counts towards the average even when it contributes a zero.
        ran[idx] = True
        company = r["company_name"]
        if r["watchlist_tier"] and company and company != "Unknown":
            buckets[idx][company] += 1

    return [b for b, did_run in zip(buckets, ran) if did_run]


def _collection_label(start: datetime | None, end: datetime | None) -> str:
    """Heading that describes when the data was gathered.

    Previously this was simply today's date, which claimed freshness the data did
    not have — a dashboard opened a week after the last scrape still announced
    today. Now it names the actual collection date, and a range when the signals
    were gathered across more than one day.
    """
    if start is None or end is None:
        return "Sample dataset"
    if start.date() == end.date():
        return f"Week of {_fmt_day(end)}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day} – {_fmt_day(end)}"
    return f"{_fmt_day(start)} – {_fmt_day(end)}"


def shape_signal(r: dict[str, Any], index: int) -> dict[str, Any]:
    """One database row as the dashboard's `Signal`.

    Shared by the digest and the feed. They select different rows and order them
    differently, but a signal must *describe* itself identically in both — two
    copies of this would drift the first time either changed.
    """
    raw = r.get("raw_content") or ""
    tier = r.get("watchlist_tier")
    is_new = bool(r.get("is_new_prospect"))
    sector_key = r.get("sector") or "other"
    title, desc = _title_and_desc(raw)
    return {
        "id": r.get("signal_id") or f"sig-{index:03d}",
        # `n` is assigned after selection — both callers reorder afterwards.
        # The scrapers already know their market — pngworkforce is PNG, seek and
        # adzuna are AU — so use that as the baseline and let the keywords only
        # promote a row to PNG. Inferring from text alone put 15 PNGworkforce
        # jobs under "Australia" purely because their teaser named no PNG
        # landmark, while still correctly catching a PNG role advertised on SEEK.
        "region": infer_geography(raw, default=(r.get("geography") or "AU")),
        "tier": tier,
        "company": r.get("company_name") or "Unknown",
        "title": title,
        "desc": desc,
        "action": (r.get("analysis_notes") or "").strip() or None,
        "sector": SECTOR_PRETTY.get(sector_key, sector_key.title()),
        "source": r.get("source_name") or "pngworkforce",
        "category": r.get("signal_category") or "hiring_velocity",
        "cycle": (r.get("review_cycle") or "weekly").upper(),
        "conf": _confidence(index, tier, is_new),
        "_rank": rank_signal(r.get("signal_category"), len(raw), tier),
    }


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

    collected_from, collected_to = _collection_span(rows)

    # The velocity table compares a window against the windows before it, so it
    # needs a fixed window rather than "whatever rows we are showing". When the
    # requested window was empty we fell back to older signals, and anchoring on
    # `now` would then count a month of history as "this week".
    #
    # The boundary is snapped to a day, not to the last timestamp. Two runs a
    # week apart started at 23:40 and 23:14 — so a timestamp-anchored boundary
    # sat 26 minutes *before* the earlier scrape and swallowed the entire prior
    # week into "this week", leaving nothing to compare against. Scrapes never
    # start at the same minute; the window must not depend on it.
    velocity_end = _day_after(collected_to or datetime.now(timezone.utc))
    velocity_start = velocity_end - timedelta(days=days)

    signals: list[dict[str, Any]] = []
    velocity_counter: Counter[str] = Counter()
    velocity_meta: dict[str, dict[str, Any]] = {}
    new_names: dict[str, dict[str, Any]] = {}

    for i, r in enumerate(rows):
        raw = r.get("raw_content") or ""
        signal = shape_signal(r, i)
        region, company, sector, desc = (
            signal["region"], signal["company"], signal["sector"], signal["desc"]
        )
        tier = r.get("watchlist_tier")
        is_new = bool(r.get("is_new_prospect"))
        signals.append(signal)

        if tier and company != "Unknown":
            captured = _parse_ts(r.get("captured_at"))
            # Synthetic rows carry no timestamp; they are all "this week" by
            # definition, and get no baseline anyway.
            if captured is None or captured >= velocity_start:
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

    # The baseline is measured, not assumed. This used to be `round(n * 0.7)`,
    # which made every company appear to be hiring ~43% above its own average —
    # including one that had never been seen before. A trend that is derived
    # from this week's number is not a trend.
    history = (
        _history_windows(db_path, window_start=velocity_start, days=days)
        if source_mode == "live"
        else []
    )

    velocity = []
    for co, n in velocity_counter.most_common(10):
        if history:
            avg: float | None = round(sum(h.get(co, 0) for h in history) / len(history), 1)
            # A company with no prior signals has no percentage to be up by;
            # `None` tells the UI to say "new" instead of dividing by zero.
            change = round((n - avg) / avg * 100) if avg else None
            trend = [h.get(co, 0) for h in history] + [n]
        else:
            avg, change, trend = None, None, [n]
        velocity.append({
            "co": co,
            "wk": n,
            "avg": avg,
            "change": change,
            #: How many earlier windows the average covers. 0 means there is no
            #: history yet and the UI must not imply a comparison.
            "basis": len(history),
            "trend": trend,
            "sector": velocity_meta[co]["sector"],
            "tier": velocity_meta[co]["tier"],
        })

    # Pick the 40 shown rows the same way the Slack digest picks its 10: strongest
    # signals first, balanced across regions.
    #
    # Previously this was `signals[:40]` over a captured_at DESC list. The two
    # scrapers finish a second apart, so every SEEK row sorted ahead of every
    # PNGworkforce row and the cut landed inside SEEK — the entire Papua New
    # Guinea section disappeared despite having 50 signals.
    by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in signals:
        # Within a region, round-robin the sources too. Ranking alone gave every
        # Australian slot to Adzuna, whose ~610-character records always beat
        # SEEK's ~245 on the length tiebreak — SEEK disappeared from the section
        # despite having the most AU signals.
        by_region[s["region"]].append(s)

    ordered_by_region: dict[str, list[dict[str, Any]]] = {}
    for region, rows_in_region in by_region.items():
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for s in rows_in_region:
            by_source[s["source"]].append(s)
        for src in by_source:
            by_source[src].sort(key=lambda s: s["_rank"])
        ordered_by_region[region] = interleave(by_source, len(rows_in_region))

    shown = interleave_regions(ordered_by_region, MAX_SIGNALS_SHOWN)
    for i, s in enumerate(shown):
        s["n"] = f"{i + 1:02d}"
    for s in signals:
        s.pop("_rank", None)

    classified = len(signals)
    geos = Counter(s["region"] for s in signals)
    week_label = _collection_label(collected_from, collected_to)

    return {
        "sourceMode": source_mode,
        #: Length of the capture window these figures cover.
        "windowDays": days,
        #: True when the window held nothing and these are older signals instead.
        #: The UI must say so rather than presenting them as this week's.
        "windowEmpty": window_empty,
        #: When the rendered signals were actually scraped — NOT when this payload
        #: was built. The heading used to show today's date, which read as "this
        #: data is from today" even when the last scrape was a week ago.
        "collectedFrom": collected_from.isoformat() if collected_from else None,
        "collectedTo": collected_to.isoformat() if collected_to else None,
        "week": (collected_to or datetime.now(timezone.utc)).strftime("WEEK %d %b %Y").upper(),
        "weekLabel": week_label,
        "generatedAt": datetime.now(timezone.utc).strftime("%a %d %b %Y · %H:%M UTC"),
        "kpis": {
            "rolesThisWeek": {"val": classified, "delta": f"AU {geos.get('AU', 0)} / PNG {geos.get('PNG', 0)}", "dir": "up"},
            "newSignals": {"val": classified, "delta": "reviewed", "dir": "up"},
            "newNames": {"val": len(new_names), "delta": f"{len(new_names)} to review", "dir": "flat"},
            "pushQueries": {"val": 0, "delta": "—", "dir": "flat"},
        },
        "signals": shown,
        "velocity": velocity,
        "newNames": list(new_names.values())[:8],
    }

# --------------------------------------------------------------------------
# The Signal Feed: everything, paginated
# --------------------------------------------------------------------------

#: Rows per page. Large enough that paging is rare, small enough that a page
#: renders instantly.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _matches(signal: dict[str, Any], region: str | None, cycle: str | None,
             terms: list[str]) -> bool:
    if region and signal["region"] != region:
        return False
    if cycle and signal["cycle"] != cycle:
        return False
    if not terms:
        return True
    haystack = " ".join(str(signal.get(k) or "") for k in
                        ("company", "title", "desc", "sector", "region", "source", "tier")).lower()
    # Every term must appear, so "bhp mining" narrows rather than widening.
    return all(t in haystack for t in terms)


def scraped_all_time(target: str | Path | None = None) -> int:
    """Every row ever ingested, classified or not — what the scrapers have
    collected in total, which is what "signals scraped" means to a reader."""
    resolved = resolve_target(target)
    if not is_postgres(resolved) and not Path(resolved).exists():
        return 0
    try:
        with connect(resolved) as conn:
            return int(conn.execute("SELECT count(*) FROM signals").fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        log.warning("feed: could not count signals (%s)", exc)
        return 0


def build_feed_payload(
    db_path: str | Path | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    region: str | None = None,
    cycle: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    """One page of the full signal list, newest first.

    Unlike the digest this is not windowed, ranked or capped: the feed is the
    browse-everything view, and the digest is a selection from it.

    Filtering happens here rather than in the browser because the two must agree
    — filtering a single page client-side would search 50 of 250 rows and report
    "3 results" for a term with 40 matches.

    It is applied *after* shaping rather than in SQL because `region` is not a
    column: PNG roles advertised on an Australian board are promoted by keyword,
    so a `WHERE geography = ?` would disagree with the region shown on the row.
    That means every classified row is loaded per request. At a few thousand
    rows that is milliseconds; if this ever holds a year of daily scrapes, the
    region should be denormalised into a column and this moved into SQL.
    """
    rows = _rows_from_db(db_path)
    shaped = [shape_signal(r, i) for i, r in enumerate(rows)]
    for s in shaped:
        s.pop("_rank", None)

    terms = [t for t in (q or "").strip().lower().split() if t]
    matched = [s for s in shaped
               if _matches(s, (region or "").upper() or None, (cycle or "").upper() or None, terms)]

    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    page = matched[offset:offset + limit]
    # Numbering is absolute, so row 51 reads "51" on page two rather than "01".
    for i, s in enumerate(page):
        s["n"] = f"{offset + i + 1:02d}"

    return {
        "signals": page,
        #: Rows matching the current filter — what pagination walks through.
        "total": len(matched),
        #: Every classified row, ignoring filters.
        "totalClassified": len(shaped),
        #: Every row the scrapers have ever collected, including any not yet
        #: classified. This is the "all time" figure the feed header shows.
        "scrapedAllTime": scraped_all_time(db_path),
        "limit": limit,
        "offset": offset,
    }
