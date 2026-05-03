"""Weekly digest formatter. Produces Slack mrkdwn from classified signals."""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

# Geography inference keywords (raw_content substring match, case-insensitive).
PNG_KEYWORDS = (
    "png", "lihir", "porgera", "tabubil", "ok tedi", "port moresby", "pom",
    "hides", "kutubu", "niu ailan", "morobe", "western province",
    "papua new guinea", "papua lng", "png lng",
)


def infer_geography(raw_content: str, default: str = "AU") -> str:
    text = (raw_content or "").lower()
    return "PNG" if any(k in text for k in PNG_KEYWORDS) else default


# --------------------------------------------------------------------------
# Section builders
# --------------------------------------------------------------------------


SECTOR_PRETTY = {
    "mining": "Mining",
    "oil_gas": "O&G",
    "construction": "Construction",
    "defence": "Defence",
    "energy_transition": "Energy Transition",
    "other": "Other",
}


def _header(week_of: datetime) -> str:
    return f":large_blue_circle: *MIOS Weekly Intelligence — Week of {week_of.strftime('%-d %B %Y') if hasattr(week_of, 'strftime') else week_of}*"


def _key_signals_section(signals: list[sqlite3.Row], max_items: int = 10) -> str:
    """Group up to `max_items` of the most informative classified signals by geography."""
    # Prioritise: leadership > project > financial > competitive > hiring_velocity > market_intel
    rank = {"leadership": 0, "project": 1, "financial": 2, "competitive": 3,
            "hiring_velocity": 4, "market_intel": 5}
    by_geo: dict[str, list] = defaultdict(list)
    for r in signals:
        geo = infer_geography(r["raw_content"])
        by_geo[geo].append(r)

    lines = [":red_circle: *Key Signals This Week*"]
    chosen_total = 0
    for geo_label, geo_key in (("AUSTRALIA", "AU"), ("PAPUA NEW GUINEA", "PNG")):
        bucket = sorted(
            by_geo.get(geo_key, []),
            key=lambda r: (rank.get(r["signal_category"] or "hiring_velocity", 9),
                           -len(r["raw_content"] or "")),
        )
        if not bucket:
            continue
        lines.append(f"*{geo_label}*")
        for r in bucket:
            if chosen_total >= max_items:
                break
            company = r["company_name"] or "Unknown"
            cat = (r["signal_category"] or "signal").replace("_", " ")
            tier = f" _(Tier {r['watchlist_tier']})_" if r["watchlist_tier"] else ""
            note = (r["analysis_notes"] or "").strip()
            lines.append(f"• *{company}*{tier} — {cat}. {note}")
            chosen_total += 1
        if chosen_total >= max_items:
            break
    if chosen_total == 0:
        lines.append("_No classified signals in the reporting window._")
    return "\n".join(lines)


def _market_pulse_section(signals: list[sqlite3.Row]) -> str:
    sectors = Counter(r["sector"] for r in signals if r["sector"])
    geos = Counter(infer_geography(r["raw_content"]) for r in signals)
    cycles = Counter(r["review_cycle"] for r in signals if r["review_cycle"])
    new_prospects = sum(1 for r in signals if r["is_new_prospect"])
    total = len(signals)
    bullets = [":large_green_circle: *Market Pulse*"]
    if total:
        top_sector, top_n = (sectors.most_common(1) or [(None, 0)])[0]
        if top_sector:
            bullets.append(
                f"• Total classified signals this week: *{total}* — top sector "
                f"*{SECTOR_PRETTY.get(top_sector, top_sector)}* ({top_n})."
            )
        au, png = geos.get("AU", 0), geos.get("PNG", 0)
        bullets.append(f"• Geographic split: *AU {au}* / *PNG {png}*.")
        if cycles:
            cycles_str = ", ".join(f"{k} {v}" for k, v in cycles.most_common())
            bullets.append(f"• Review-cycle mix: {cycles_str}.")
        if new_prospects:
            bullets.append(f"• :seedling: *{new_prospects}* new prospect(s) detected — see New Names table.")
        else:
            bullets.append("• No new prospects identified outside the watchlist this week.")
    else:
        bullets.append("_No data this week._")
    return "\n".join(bullets)


def _hiring_velocity_section(signals: list[sqlite3.Row], top_n: int = 10) -> str:
    counter: Counter[str] = Counter()
    sector_by_company: dict[str, str] = {}
    for r in signals:
        if not r["watchlist_tier"]:
            continue  # only watchlist clients in this table
        name = r["company_name"]
        if not name:
            continue
        counter[name] += 1
        sector_by_company.setdefault(name, r["sector"] or "")
    rows = counter.most_common(top_n)
    lines = [":bar_chart: *Hiring Velocity — Top 10 Watchlist Clients*"]
    if not rows:
        lines.append("_No watchlist activity this week._")
        return "\n".join(lines)
    lines.append("```")
    lines.append(f"{'Company':<24} {'This Week':>10}  {'Sector':<14}")
    lines.append("-" * 52)
    for company, n in rows:
        sector = SECTOR_PRETTY.get(sector_by_company.get(company, ""), "—")
        lines.append(f"{company[:24]:<24} {n:>10}  {sector:<14}")
    lines.append("```")
    return "\n".join(lines)


def _new_names_section(signals: list[sqlite3.Row]) -> str:
    rows = [r for r in signals if r["is_new_prospect"] and r["company_name"]]
    lines = [":new: *New Names (Not in Watchlist)*"]
    if not rows:
        lines.append("_No new prospects this week._")
        return "\n".join(lines)
    lines.append("```")
    lines.append(f"{'Company':<28} {'Sector':<14}  {'Geography':<10}")
    lines.append("-" * 56)
    seen: set[str] = set()
    for r in rows:
        name = r["company_name"]
        if name in seen:
            continue
        seen.add(name)
        sector = SECTOR_PRETTY.get(r["sector"] or "", "—")
        geo = infer_geography(r["raw_content"])
        lines.append(f"{name[:28]:<28} {sector:<14}  {geo:<10}")
    lines.append("```")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def _format_week_of(d: datetime) -> str:
    # Cross-platform safe (Windows %-d fails); strip leading zero manually
    s = d.strftime("%d %B %Y")
    return s.lstrip("0")


def build_digest(db_path: str | Path, since: datetime) -> str:
    """Build a Slack-flavoured weekly digest covering classified signals since `since`."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    since_iso = since.isoformat(timespec="seconds")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        signals = conn.execute(
            "SELECT signal_id, company_name, sector, signal_category, review_cycle, "
            "watchlist_tier, is_new_prospect, raw_content, analysis_notes, captured_at "
            "FROM signals "
            "WHERE classified_at IS NOT NULL AND captured_at >= ? "
            "ORDER BY captured_at DESC",
            (since_iso,),
        ).fetchall()

    week_of = since + timedelta(days=0)
    sections = [
        f":large_blue_circle: *MIOS Weekly Intelligence — Week of {_format_week_of(week_of)}*",
        _key_signals_section(signals),
        _market_pulse_section(signals),
        _hiring_velocity_section(signals),
        _new_names_section(signals),
    ]
    return "\n\n".join(sections)
