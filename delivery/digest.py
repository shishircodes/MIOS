"""Weekly digest formatter. Produces Slack mrkdwn from classified signals."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from delivery import ranking
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


def _field(row: Any, key: str) -> Any:
    """A column that may not have been selected, from a row of either engine."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _region(row: Any) -> str:
    """The market a signal belongs to, resolved the way the digest page does:
    what was stored at ingest, else the collector's own market corrected by the
    PNG keywords."""
    return _field(row, "region") or infer_geography(
        row["raw_content"], default=(_field(row, "geography") or "AU"))


def _title(raw_content: str) -> str:
    raw = (raw_content or "").strip()
    return (raw.split("|", 1)[0].strip() if "|" in raw else raw[:80])[:90]


def _key_signals_section(signals: list[Any], max_items: int = 10) -> str:
    """The strongest signals, ranked the way the digest page ranks them.

    See `delivery.ranking`: a company's job ads are one line, every line is
    scored, and the strongest are taken with a limit per company and a share
    kept for each market.
    """
    items = [
        ranking.Item(
            key=str(_field(r, "signal_id") or i),
            company=(r["company_name"] or "").strip(),
            region=_region(r),
            category=r["signal_category"] or "hiring_velocity",
            kind=_field(r, "source_type") or "job_board",
            tier=r["watchlist_tier"],
            is_new=bool(r["is_new_prospect"]),
            captured_at=str(_field(r, "captured_at") or ""),
            title=_title(r["raw_content"]),
            ref=r,
        )
        for i, r in enumerate(signals)
    ]
    chosen = ranking.rank(items, max_items)

    # Chosen by score across both markets; presented grouped by market.
    lines = [":red_circle: *Key Signals This Week*"]
    for geo_label, geo_key in (("AUSTRALIA", "AU"), ("PAPUA NEW GUINEA", "PNG")):
        bucket = [ln for ln in chosen if ln.lead.region == geo_key]
        if not bucket:
            continue
        lines.append(f"*{geo_label}*")
        for ln in bucket:
            r = ln.lead.ref
            company = r["company_name"] or "Unknown"
            tier = f" _(Tier {r['watchlist_tier']})_" if r["watchlist_tier"] else ""
            if ln.folded:
                lines.append(f"• *{company}*{tier} — {ln.count} roles advertised: "
                             f"{ranking.roles_summary(ln)}")
                continue
            cat = (r["signal_category"] or "signal").replace("_", " ")
            note = (r["analysis_notes"] or "").strip()
            lines.append(f"• *{company}*{tier} — {cat}. {note}")
    if not chosen:
        lines.append("_No classified signals in the reporting window._")
    return "\n".join(lines)


def _market_pulse_section(pulse: list[dict[str, str]] | None) -> str:
    """The week's written read, or nothing at all.

    Returns an empty string when there is no generated pulse, and the caller
    drops the section entirely. There is deliberately no computed fallback: this
    section exists to say what the numbers *mean*, and a template restating the
    numbers in the place a written summary would go implies a judgement nobody
    made. An absent section is honest; a manufactured one is not.
    """
    if not pulse:
        return ""
    lines = [":large_green_circle: *Market Pulse*"]
    for b in pulse:
        text = (b.get("text") or "").strip()
        if not text:
            continue
        # Interpretation is allowed to be here, but never unlabelled — a reader
        # deciding who to call is entitled to know which bullets are measured
        # and which are a reading of them.
        mark = " _(interpretation)_" if b.get("kind") == "interpretation" else ""
        lines.append(f"• {text}{mark}")
    return "\n".join(lines) if len(lines) > 1 else ""


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


def build_digest(
    db_path: str | Path | None,
    since: datetime,
    pulse: list[dict[str, str]] | None = None,
    run_id: str | None = None,
) -> str:
    """Build a Slack-flavoured weekly digest.

    With `run_id`, it covers exactly the signals that pipeline run collected, so
    the message posted to Slack contains the same signals as the digest stored
    for that run. Without it, it covers everything captured since `since`, which
    is the behaviour every existing caller relies on.

    `db_path` may be a SQLite path, a Postgres DSN, or None to use configuration.

    `pulse` is the generated Market Pulse, passed in by the pipeline that just
    produced it rather than re-read from storage — the caller already has it,
    and looking it up again only creates a way for the two to disagree. Omitted
    means no Market Pulse section, which is what a failed generation looks like.
    """
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    since_iso = since.isoformat(timespec="seconds")

    COLUMNS = ("SELECT signal_id, company_name, sector, signal_category, review_cycle, "
               "watchlist_tier, is_new_prospect, raw_content, analysis_notes, captured_at, "
               # What the ranking needs beyond the text: the kind of item, so
               # only job ads are folded, and its market as stored at ingest.
               "source_type, geography, region "
               "FROM signals WHERE classified_at IS NOT NULL "
               # Outside the five sectors: never a key signal, a velocity row or
               # a new name, the same as the digest page.
               "AND COALESCE(sector, '') <> 'other' ")
    with connect(db_path) as conn:
        if run_id is not None:
            signals = conn.execute(
                COLUMNS + "AND run_id = ? ORDER BY captured_at DESC", (run_id,)
            ).fetchall()
        else:
            signals = conn.execute(
                COLUMNS + "AND captured_at >= ? ORDER BY captured_at DESC", (since_iso,)
            ).fetchall()

    week_of = since + timedelta(days=0)
    sections = [
        f":large_blue_circle: *MIOS Weekly Intelligence — Week of {_format_week_of(week_of)}*",
        _key_signals_section(signals),
        _market_pulse_section(pulse),
        _hiring_velocity_section(signals),
        _new_names_section(signals),
    ]
    # An empty section is dropped rather than left as a blank gap between two
    # populated ones — a week with no Market Pulse should read as four sections,
    # not as four sections and a hole.
    return "\n\n".join(s for s in sections if s)
