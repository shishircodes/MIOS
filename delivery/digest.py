"""Weekly digest formatter. Produces Slack mrkdwn from classified signals."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

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


#: Signal categories, most newsworthy first. A leadership change says more about
#: an employer's direction than another routine vacancy, so it earns a slot first.
SIGNAL_RANK = {
    "leadership": 0, "project": 1, "financial": 2,
    "competitive": 3, "hiring_velocity": 4, "market_intel": 5,
}

#: Region display order. Anything outside this list is appended after these.
REGION_ORDER = ("AU", "PNG")


def rank_signal(category: str | None, weight: int) -> tuple[int, int]:
    """Sort key: category importance first, longer content as the tiebreak."""
    return (SIGNAL_RANK.get(category or "hiring_velocity", 9), -weight)


def interleave_regions(buckets: dict[str, list], limit: int) -> list:
    """Take up to `limit` items round-robin across regions.

    Replaces "fill from AU, then PNG if any budget is left". That shared counter
    meant a busy AU week consumed the entire quota and Papua New Guinea vanished
    from the digest — not because it had no signals, but because it was second in
    the loop. Round-robin guarantees every region with data is represented, and
    because each bucket is pre-sorted, the items taken are still its strongest.
    """
    order = [r for r in REGION_ORDER if buckets.get(r)]
    order += [r for r in buckets if r not in REGION_ORDER and buckets[r]]

    out: list = []
    cursor = {r: 0 for r in order}
    while len(out) < limit and any(cursor[r] < len(buckets[r]) for r in order):
        for region in order:
            if len(out) >= limit:
                break
            i = cursor[region]
            if i < len(buckets[region]):
                out.append(buckets[region][i])
                cursor[region] = i + 1
    return out


def _key_signals_section(signals: list[Any], max_items: int = 10) -> str:
    """The strongest classified signals, balanced across geographies."""
    by_geo: dict[str, list] = defaultdict(list)
    for r in signals:
        by_geo[infer_geography(r["raw_content"])].append(r)

    for region in by_geo:
        by_geo[region].sort(
            key=lambda r: rank_signal(r["signal_category"], len(r["raw_content"] or ""))
        )

    chosen = interleave_regions(by_geo, max_items)

    # Selection is balanced; presentation is still grouped by region.
    grouped: dict[str, list] = defaultdict(list)
    for r in chosen:
        grouped[infer_geography(r["raw_content"])].append(r)

    lines = [":red_circle: *Key Signals This Week*"]
    for geo_label, geo_key in (("AUSTRALIA", "AU"), ("PAPUA NEW GUINEA", "PNG")):
        bucket = grouped.get(geo_key)
        if not bucket:
            continue
        lines.append(f"*{geo_label}*")
        for r in bucket:
            company = r["company_name"] or "Unknown"
            cat = (r["signal_category"] or "signal").replace("_", " ")
            tier = f" _(Tier {r['watchlist_tier']})_" if r["watchlist_tier"] else ""
            note = (r["analysis_notes"] or "").strip()
            lines.append(f"• *{company}*{tier} — {cat}. {note}")
    if not chosen:
        lines.append("_No classified signals in the reporting window._")
    return "\n".join(lines)


def _market_pulse_section(signals: list[Any]) -> str:
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


def _hiring_velocity_section(signals: list[Any], top_n: int = 10) -> str:
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


def _new_names_section(signals: list[Any]) -> str:
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


def build_digest(db_path: str | Path | None, since: datetime) -> str:
    """Build a Slack-flavoured weekly digest covering classified signals since `since`.

    `db_path` may be a SQLite path, a Postgres DSN, or None to use configuration.
    """
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    since_iso = since.isoformat(timespec="seconds")

    with connect(db_path) as conn:
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
