"""Weekly digest formatter. Produces Slack mrkdwn from classified signals."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loader.db import connect


# Geography inference keywords (raw_content substring match, case-insensitive).
PNG_KEYWORDS = (
    "png",
    "lihir",
    "porgera",
    "tabubil",
    "ok tedi",
    "port moresby",
    "pom",
    "hides",
    "kutubu",
    "niu ailan",
    "morobe",
    "western province",
    "papua new guinea",
    "papua lng",
    "png lng",
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
    return (
        f":large_blue_circle: *MIOS Weekly Intelligence — Week of "
        f"{week_of.strftime('%-d %B %Y') if hasattr(week_of, 'strftime') else week_of}*"
    )


#: Signal categories, most newsworthy first. A leadership change says more about
#: an employer's direction than another routine vacancy, so it earns a slot first.
SIGNAL_RANK = {
    "leadership": 0,
    "project": 1,
    "financial": 2,
    "competitive": 3,
    "hiring_velocity": 4,
    "market_intel": 5,
}


#: Region display order. Anything outside this list is appended after these.
REGION_ORDER = ("AU", "PNG")


#: Watchlist tiers, most important first. Untiered companies sort last.
TIER_RANK = {"A": 0, "B": 1, "C": 2}


def rank_signal(
    category: str | None,
    weight: int,
    tier: str | None = None,
) -> tuple[int, int, int]:
    """Sort key: category, then watchlist tier, then content length.

    Length used to be the only tiebreak, which quietly ranked sources by how
    verbose their listings are — Adzuna's ~610-character records displaced every
    SEEK record at ~245, so an entire source vanished from the digest. Tier is
    the honest second key: a Tier A employer hiring is a stronger signal than a
    longer advertisement.
    """
    return (
        SIGNAL_RANK.get(category or "hiring_velocity", 9),
        TIER_RANK.get((tier or "").upper(), 9),
        -weight,
    )


def interleave(
    buckets: dict[str, list],
    limit: int,
    order: tuple[str, ...] = (),
) -> list:
    """Take up to `limit` items round-robin across buckets.

    Used twice: across regions, and across sources within a region. Both exist to
    stop one bucket consuming the whole quota — "fill from AU, then PNG with
    what's left" hid Papua New Guinea entirely on a busy AU week, and sorting by
    content length handed every Australian slot to whichever source writes the
    longest blurbs. Round-robin guarantees each bucket with data is represented,
    and since buckets are pre-sorted, the items taken are still its strongest.

    `order` fixes the leading keys; anything else follows in dict order.
    """
    keys = [k for k in order if buckets.get(k)]
    keys += [k for k in buckets if k not in order and buckets[k]]

    out: list = []
    cursor = {k: 0 for k in keys}

    while len(out) < limit and any(
        cursor[k] < len(buckets[k]) for k in keys
    ):
        for key in keys:
            if len(out) >= limit:
                break

            i = cursor[key]

            if i < len(buckets[key]):
                out.append(buckets[key][i])
                cursor[key] = i + 1

    return out


def interleave_regions(
    buckets: dict[str, list],
    limit: int,
) -> list:
    """Round-robin across geographies, AU then PNG first."""
    return interleave(buckets, limit, REGION_ORDER)


def _key_signals_section(
    signals: list[Any],
    max_items: int = 10,
) -> str:
    """The strongest classified signals, balanced across geographies."""

    by_geo: dict[str, list] = defaultdict(list)

    for r in signals:
        by_geo[infer_geography(r["raw_content"])].append(r)

    for region in by_geo:
        by_geo[region].sort(
            key=lambda r: rank_signal(
                r["signal_category"],
                len(r["raw_content"] or ""),
                r["watchlist_tier"],
            )
        )

    chosen = interleave_regions(by_geo, max_items)

    # Selection is balanced; presentation is still grouped by region.
    grouped: dict[str, list] = defaultdict(list)

    for r in chosen:
        grouped[infer_geography(r["raw_content"])].append(r)

    lines = [":red_circle: *Key Signals This Week*"]

    for geo_label, geo_key in (
        ("AUSTRALIA", "AU"),
        ("PAPUA NEW GUINEA", "PNG"),
    ):
        bucket = grouped.get(geo_key)

        if not bucket:
            continue

        lines.append(f"*{geo_label}*")

        for r in bucket:
            company = r["company_name"] or "Unknown"
            cat = (r["signal_category"] or "signal").replace("_", " ")

            tier = (
                f" _(Tier {r['watchlist_tier']})_"
                if r["watchlist_tier"]
                else ""
            )

            note = (r["analysis_notes"] or "").strip()

            lines.append(
                f"• *{company}*{tier} — {cat}. {note}"
            )

    if not chosen:
        lines.append(
            "_No classified signals in the reporting window._"
        )

    return "\n".join(lines)


def _market_pulse_section(
    pulse: list[dict[str, str]] | None,
) -> str:
    """The week's written read, or nothing at all.

    Returns an empty string when there is no generated pulse, and the caller
    drops the section entirely. There is deliberately no computed fallback:
    this section exists to say what the numbers *mean*, and a template
    restating the numbers in the place a written summary would go implies a
    judgement nobody made. An absent section is honest; a manufactured one is
    not.
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
        mark = (
            " _(interpretation)_"
            if b.get("kind") == "interpretation"
            else ""
        )

        lines.append(f"• {text}{mark}")

    return "\n".join(lines) if len(lines) > 1 else ""


def _hiring_velocity_section(
    velocity: list[dict[str, Any]],
    top_n: int = 10,
) -> str:
    rows = velocity[:top_n]

    lines = [
        ":bar_chart: *Hiring Velocity — Top 10 Watchlist Clients*"
    ]

    if not rows:
        lines.append("_No watchlist activity this week._")
        return "\n".join(lines)

    lines.append("```")

    lines.append(
        f"{'Company':<24} "
        f"{'This Week':>10}  "
        f"{'Movement':>10}  "
        f"{'Sector':<14}"
    )

    lines.append("-" * 64)

    for row in rows:
        company = row["co"]
        change = row["change"]

        # Companies without a historical baseline are shown as "new"
        # rather than as a percentage increase from zero.
        movement = (
            "new"
            if change is None
            else f"{change:+d}%"
        )

        sector = SECTOR_PRETTY.get(
            row.get("sector", ""),
            "—",
        )

        lines.append(
            f"{company[:24]:<24} "
            f"{row['wk']:>10}  "
            f"{movement:>10}  "
            f"{sector:<14}"
        )

    lines.append("```")

    return "\n".join(lines)


def _new_names_section(signals: list[Any]) -> str:
    rows = [
        r
        for r in signals
        if r["is_new_prospect"] and r["company_name"]
    ]

    lines = [":new: *New Names (Not in Watchlist)*"]

    if not rows:
        lines.append("_No new prospects this week._")
        return "\n".join(lines)

    lines.append("```")

    lines.append(
        f"{'Company':<28} "
        f"{'Sector':<14}  "
        f"{'Geography':<10}"
    )

    lines.append("-" * 56)

    seen: set[str] = set()

    for r in rows:
        name = r["company_name"]

        if name in seen:
            continue

        seen.add(name)

        sector = SECTOR_PRETTY.get(
            r["sector"] or "",
            "—",
        )

        geo = infer_geography(
            r["raw_content"]
        )

        lines.append(
            f"{name[:28]:<28} "
            f"{sector:<14}  "
            f"{geo:<10}"
        )

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
) -> str:
    """Build a Slack-flavoured weekly digest covering classified signals since `since`.

    `db_path` may be a SQLite path, a Postgres DSN, or None to use
    configuration.

    `pulse` is the generated Market Pulse, passed in by the pipeline that just
    produced it rather than re-read from storage — the caller already has it,
    and looking it up again only creates a way for the two to disagree.
    Omitted means no Market Pulse section, which is what a failed generation
    looks like.
    """

    if since.tzinfo is None:
        since = since.replace(
            tzinfo=timezone.utc
        )

    since_iso = since.isoformat(
        timespec="seconds"
    )

    with connect(db_path) as conn:
        signals = conn.execute(
            "SELECT signal_id, company_name, sector, signal_category, "
            "review_cycle, watchlist_tier, is_new_prospect, raw_content, "
            "analysis_notes, captured_at "
            "FROM signals "
            "WHERE classified_at IS NOT NULL AND captured_at >= ? "
            "ORDER BY captured_at DESC",
            (since_iso,),
        ).fetchall()

    # Imported here because digest_service reuses ranking helpers
    # from this module.
    from api.digest_service import build_digest_payload

    days = max(
        1,
        (
            datetime.now(timezone.utc)
            - since
        ).days,
    )

    velocity = build_digest_payload(
        db_path,
        days=days,
    )["velocity"]

    week_of = since + timedelta(days=0)

    sections = [
        (
            ":large_blue_circle: *MIOS Weekly Intelligence — "
            f"Week of {_format_week_of(week_of)}*"
        ),
        _key_signals_section(signals),

        # Keep the latest Market Pulse behaviour from main.
        _market_pulse_section(pulse),

        # Use the existing velocity payload so wk/change are not
        # recalculated inside the Slack formatter.
        _hiring_velocity_section(velocity),

        _new_names_section(signals),
    ]

    # An empty section is dropped rather than left as a blank gap between two
    # populated ones — a week with no Market Pulse should read as four
    # sections, not as four sections and a hole.
    return "\n\n".join(
        s for s in sections if s
    )