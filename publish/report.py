"""Assemble a quarterly market report from the signals MIOS has collected.

Mode Publish turns the intelligence into something a client reads: a quarterly
report on hiring trends across Australia and Papua New Guinea.

Two rules shape this module.

**Every number traces to a row.** No figure here is estimated, rounded up for
effect, or carried over from a previous quarter. If the data cannot support a
claim, the claim is not made — the Hiring Velocity table taught that lesson
expensively, where a baseline of `this_week × 0.7` produced a confident "+42%"
that meant nothing.

**Where judgement is required, a human writes it.** The outlook section is
generated empty and marked `manual`, because "what happens next quarter" is not
a count. A report cannot be approved while it is still blank, so the gap cannot
be published by accident.

Generation is deterministic: no LLM. The same quarter over the same rows always
produces the same prose, which is what makes a client-facing document reviewable.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.digest_service import SECTOR_PRETTY
from delivery.digest import infer_geography
from loader.db import connect, is_postgres, resolve_target
from push.profile_parser import ROLE_KEYWORDS

log = logging.getLogger(__name__)

#: Sections that carry a heading but no computed prose. The reviewer supplies
#: the text; until they do, the report cannot be approved.
MANUAL_SECTIONS = ("Looking Ahead",)

#: Below this, percentages start to mislead — three signals is not a trend, and
#: "67% of activity" from two rows reads far stronger than it is.
MIN_SIGNALS_FOR_PERCENTAGES = 10

#: How many companies and roles each section names.
TOP_N = 6


@dataclass
class Section:
    heading: str
    body: str
    source: str = "generated"


@dataclass
class QuarterData:
    """Everything the prose is allowed to draw on, counted once."""

    quarter: str
    total: int = 0
    window_from: str | None = None
    window_to: str | None = None
    by_sector: Counter[str] = field(default_factory=Counter)
    by_region: Counter[str] = field(default_factory=Counter)
    by_source: Counter[str] = field(default_factory=Counter)
    by_category: Counter[str] = field(default_factory=Counter)
    #: (region, sector) -> signal count, including rows with no named employer.
    by_region_sector: Counter[tuple[str, str]] = field(default_factory=Counter)
    #: (region, sector) -> company -> count
    companies: dict[tuple[str, str], Counter[str]] = field(default_factory=dict)
    #: (region, sector) -> role -> count
    roles: dict[tuple[str, str], Counter[str]] = field(default_factory=dict)
    roles_overall: Counter[str] = field(default_factory=Counter)


# --------------------------------------------------------------------------
# Quarters
# --------------------------------------------------------------------------


_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$")


def quarter_of(when: datetime) -> str:
    return f"{when.year}-Q{(when.month - 1) // 3 + 1}"


def current_quarter() -> str:
    return quarter_of(datetime.now(timezone.utc))


def quarter_bounds(quarter: str) -> tuple[datetime, datetime]:
    """Inclusive start, exclusive end. Raises ValueError on a bad label."""
    m = _QUARTER.match(quarter or "")
    if not m:
        raise ValueError(f"expected a quarter like 2026-Q3, got {quarter!r}")
    year, q = int(m.group(1)), int(m.group(2))
    start_month = (q - 1) * 3 + 1
    start = datetime(year, start_month, 1, tzinfo=timezone.utc)
    end = (datetime(year + 1, 1, 1, tzinfo=timezone.utc) if q == 4
           else datetime(year, start_month + 3, 1, tzinfo=timezone.utc))
    return start, end


# --------------------------------------------------------------------------
# Gathering
# --------------------------------------------------------------------------


def _role_in(text: str) -> str | None:
    """The role a posting is for, using Mode Push's vocabulary.

    Reusing that list rather than inventing a second one keeps the report and
    the matcher talking about the same job titles — otherwise the report could
    report demand for a role Push has never heard of.
    """
    low = text.lower()
    for role in ROLE_KEYWORDS:
        if role in low:
            return role
    return None


def gather(quarter: str, target: str | Path | None = None) -> QuarterData:
    """Count everything the report needs, in one pass over the quarter's rows."""
    start, end = quarter_bounds(quarter)
    data = QuarterData(quarter=quarter)

    resolved = resolve_target(target)
    if not is_postgres(resolved) and not Path(resolved).exists():
        return data

    try:
        with connect(resolved) as conn:
            rows = conn.execute(
                "SELECT company_name, sector, geography, signal_category, "
                "source_name, raw_content, captured_at FROM signals "
                "WHERE classified_at IS NOT NULL "
                "AND captured_at >= ? AND captured_at < ? "
                "ORDER BY captured_at",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - unreachable database, missing table
        log.warning("publish: could not read signals for %s (%s)", quarter, exc)
        return data

    for r in rows:
        raw = r["raw_content"] or ""
        # Same rule the dashboard uses: the scraper's market is the baseline and
        # keywords may only promote a row to PNG.
        region = infer_geography(raw, default=(r["geography"] or "AU"))
        sector = r["sector"] or "other"
        company = (r["company_name"] or "").strip()

        data.total += 1
        data.by_sector[sector] += 1
        data.by_region[region] += 1
        data.by_source[r["source_name"] or "unknown"] += 1
        data.by_category[r["signal_category"] or "hiring_velocity"] += 1

        key = (region, sector)
        data.by_region_sector[key] += 1
        # "Unknown" is what the classifier writes when it could not name the
        # employer; naming it in a client report would be embarrassing.
        if company and company.lower() != "unknown":
            data.companies.setdefault(key, Counter())[company] += 1

        role = _role_in(raw)
        if role:
            data.roles.setdefault(key, Counter())[role] += 1
            data.roles_overall[role] += 1

    if rows:
        data.window_from = str(rows[0]["captured_at"])
        data.window_to = str(rows[-1]["captured_at"])

    log.info("publish.gather: %s -> %d signals", quarter, data.total)
    return data


# --------------------------------------------------------------------------
# Prose
# --------------------------------------------------------------------------


def _pct(part: int, whole: int) -> str:
    return f"{round(part / whole * 100)}%" if whole else "—"


def _listing(counter: Counter[str], limit: int = TOP_N) -> str:
    """"BHP (51), Newmont (12) and Glencore (9)" — with the counts, always.

    A bare list of names invites the reader to assume they are comparable. The
    counts are what make "BHP and Newmont are hiring" honest when one has 51
    signals and the other has 2.
    """
    top = counter.most_common(limit)
    if not top:
        return ""
    parts = [f"{name} ({n})" for name, n in top]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _sector_section(data: QuarterData, region: str, sector: str, region_label: str) -> Section:
    pretty = SECTOR_PRETTY.get(sector, sector.title())
    key = (region, sector)
    n = data.by_region_sector.get(key, 0)
    companies = data.companies.get(key, Counter())
    roles = data.roles.get(key, Counter())

    if n == 0:
        body = (
            f"No {pretty.lower()} activity was detected in {region_label} this quarter. "
            "That is an absence of signal in the sources MIOS monitors, not evidence "
            "that no hiring took place."
        )
        return Section(f"{region_label} — {pretty}", body)

    lines = [
        f"MIOS detected {n} hiring signal{'s' if n != 1 else ''} across "
        f"{len(companies)} {pretty.lower()} employer{'s' if len(companies) != 1 else ''} "
        f"in {region_label} during {data.quarter}."
    ]
    if companies:
        lines.append(f"The most active were {_listing(companies)}.")
    if roles:
        lines.append(
            f"Demand concentrated in {_listing(roles, 4)}, counted by role title "
            "across the postings collected."
        )
    return Section(f"{region_label} — {pretty}", " ".join(lines))


def build_sections(data: QuarterData) -> list[Section]:
    """The report, section by section, in reading order."""
    sections: list[Section] = []

    # ---- Executive summary ----
    if data.total == 0:
        summary = (
            f"No signals were collected during {data.quarter}. This report has no "
            "findings to present. Run the collection pipeline and regenerate."
        )
    else:
        au, png = data.by_region.get("AU", 0), data.by_region.get("PNG", 0)
        bits = [
            f"MIOS collected and classified {data.total} hiring signals during "
            f"{data.quarter}, drawn from {len(data.by_source)} sources across "
            f"Australia and Papua New Guinea."
        ]
        if data.total >= MIN_SIGNALS_FOR_PERCENTAGES:
            top_sector, top_n = data.by_sector.most_common(1)[0]
            bits.append(
                f"{SECTOR_PRETTY.get(top_sector, top_sector.title())} accounted for "
                f"{_pct(top_n, data.total)} of activity, the largest share of any sector."
            )
            bits.append(
                f"Australia contributed {_pct(au, data.total)} of signals and "
                f"Papua New Guinea {_pct(png, data.total)}."
            )
        else:
            bits.append(
                f"Australia contributed {au} and Papua New Guinea {png}. The volume "
                "this quarter is too low to express as meaningful percentages."
            )
        if data.roles_overall:
            bits.append(f"The most frequently advertised roles were {_listing(data.roles_overall, 3)}.")
        summary = " ".join(bits)
    sections.append(Section("Executive Summary", summary))

    # ---- Regional sector detail ----
    sections.append(_sector_section(data, "AU", "mining", "Australia"))
    sections.append(_sector_section(data, "AU", "construction", "Australia"))

    png_total = data.by_region.get("PNG", 0)
    if png_total:
        png_companies: Counter[str] = Counter()
        png_roles: Counter[str] = Counter()
        for (region, _sector), counter in data.companies.items():
            if region == "PNG":
                png_companies.update(counter)
        for (region, _sector), counter in data.roles.items():
            if region == "PNG":
                png_roles.update(counter)
        png_sectors = Counter()
        for (region, sector), count in data.by_region_sector.items():
            if region == "PNG":
                png_sectors[sector] += count

        parts = [
            f"Papua New Guinea produced {png_total} signals this quarter across "
            f"{len(png_companies)} identified employers."
        ]
        if png_sectors:
            named = ", ".join(
                f"{SECTOR_PRETTY.get(s, s.title())} ({n})" for s, n in png_sectors.most_common(4)
            )
            parts.append(f"By sector: {named}.")
        if png_companies:
            parts.append(f"The most active employers were {_listing(png_companies)}.")
        if png_roles:
            parts.append(f"Role demand centred on {_listing(png_roles, 4)}.")
        png_body = " ".join(parts)
    else:
        png_body = (
            "No Papua New Guinea signals were collected this quarter. PNG coverage "
            "depends on PNGworkforce and Business Advantage PNG; check both are "
            "reachable before reading this as a market slowdown."
        )
    sections.append(Section("Papua New Guinea", png_body))

    # ---- Skills demand ----
    if data.roles_overall:
        ranked = data.roles_overall.most_common(10)
        table = "\n".join(f"- {role.title()} — {n} posting{'s' if n != 1 else ''}"
                          for role, n in ranked)
        matched = sum(data.roles_overall.values())
        skills_body = (
            f"Role demand across both markets, counted by title across "
            f"{matched} of {data.total} postings:\n\n{table}\n\n"
            "Postings whose title matched none of the tracked disciplines are "
            "excluded rather than bucketed as 'other', so the counts above are "
            "understated rather than padded."
        )
    else:
        skills_body = "No recognised role titles were detected in this quarter's postings."
    sections.append(Section("Skills Demand", skills_body))

    # ---- Outlook: human judgement, deliberately empty ----
    sections.append(Section(
        "Looking Ahead",
        "",
        source="manual",
    ))

    # ---- Methodology ----
    sources = ", ".join(f"{name} ({n})" for name, n in data.by_source.most_common())
    method = (
        f"Findings are drawn from {data.total} job postings and news items collected "
        f"automatically during {data.quarter} and classified by a language model into "
        "sector, region and signal type. "
        f"Sources this quarter: {sources or 'none'}. "
        "Counts describe advertised activity detected in those sources — they are a "
        "sample of the market, not a census of it, and a company absent from this "
        "report may simply hire through channels MIOS does not monitor. "
        "No figure in this report is estimated or extrapolated."
    )
    sections.append(Section("Methodology", method))

    return sections


def generate(quarter: str, target: str | Path | None = None) -> tuple[QuarterData, list[Section]]:
    """Gather and write in one call — what the API endpoint uses."""
    data = gather(quarter, target)
    return data, build_sections(data)
