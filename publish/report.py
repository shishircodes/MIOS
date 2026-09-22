"""Assemble a quarterly market report from the signals MIOS has collected.

Mode Publish turns the intelligence into something a client reads: a quarterly
report on hiring and project activity across Australia and Papua New Guinea.

Sized like the documents it sits beside. A national quarterly labour-market
report runs to sixty-odd pages — an infographic summary, an executive summary,
a chapter per topic and a statistical appendix; a recruiter's quarterly market
update runs to ten or twenty. This one is built to the same shape at the size
the data can honestly carry, fifteen to twenty-five printed pages for a normal
quarter: key figures, a summary, a market overview, a chapter per sector, the
two markets by state and province, employers, prospects, skills, leadership,
projects and tenders, competitor activity, the outlook, method, and appendices.
A section with nothing to report says so in a line rather than being padded.

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

Bodies are plain text with three structures the app and the exports render:
paragraphs separated by a blank line, "- " bullet lists, and "|"-delimited
tables with a separator row.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.prospects import RELEVANT_SECTORS, is_agency
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

#: How many companies and roles a narrative sentence names.
TOP_N = 6
#: Rows in the employer tables.
TOP_EMPLOYERS = 10
TOP_EMPLOYERS_OVERALL = 20
#: Rows in the appendix employer list: every employer with at least this many.
APPENDIX_MIN_SIGNALS = 2
#: Headlines listed per sector and in the projects chapter.
TOP_DEVELOPMENTS = 8

#: Chapters, in reading order. The report's shape stays the same every quarter
#: so two quarters can be laid side by side.
SECTOR_ORDER = ("mining", "oil_gas", "construction", "defence", "energy_transition")

CATEGORY_LABEL = {
    "hiring_velocity": "Hiring",
    "project": "Projects",
    "leadership": "Leadership",
    "financial": "Financial",
    "competitive": "Competitive moves",
    "market_intel": "Market context",
}

SOURCE_TYPE_LABEL = {"job_board": "Job ads", "news": "News", "tender": "Tenders"}

#: States and territories, matched as whole words in a job ad's header.
_STATE_CODES = ("NSW", "VIC", "QLD", "WA", "SA", "TAS", "NT", "ACT")
_STATE_NAMES = {
    "New South Wales": "NSW", "Victoria": "VIC", "Queensland": "QLD",
    "Western Australia": "WA", "South Australia": "SA", "Tasmania": "TAS",
    "Northern Territory": "NT", "Australian Capital Territory": "ACT",
}
#: Places job boards name instead of a state. Capitals and the mining and
#: resources towns that recur in the data.
_AU_PLACES = {
    "Sydney": "NSW", "Newcastle": "NSW", "Hunter": "NSW", "Wollongong": "NSW", "Orange": "NSW",
    "Melbourne": "VIC", "Geelong": "VIC", "Ballarat": "VIC", "Bendigo": "VIC", "Latrobe": "VIC",
    "Brisbane": "QLD", "Gold Coast": "QLD", "Townsville": "QLD", "Mackay": "QLD",
    "Gladstone": "QLD", "Rockhampton": "QLD", "Moranbah": "QLD", "Mount Isa": "QLD", "Cairns": "QLD",
    "Perth": "WA", "Pilbara": "WA", "Newman": "WA", "Port Hedland": "WA", "Karratha": "WA",
    "Kalgoorlie": "WA", "Tom Price": "WA", "Kwinana": "WA", "Goldfields": "WA",
    "Adelaide": "SA", "Olympic Dam": "SA", "Roxby Downs": "SA", "Whyalla": "SA", "Port Adelaide": "SA",
    "Hobart": "TAS", "Launceston": "TAS", "Darwin": "NT", "Alice Springs": "NT", "Canberra": "ACT",
}
_STATE_FULL = {
    "NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland", "WA": "Western Australia",
    "SA": "South Australia", "TAS": "Tasmania", "NT": "Northern Territory",
    "ACT": "Australian Capital Territory",
}

#: PNG's provinces, and the towns ads name instead. Longest first, so "Western
#: Highlands" is not read as "Western".
_PNG_PROVINCES = (
    "National Capital District", "Autonomous Region of Bougainville", "Southern Highlands",
    "Western Highlands", "Eastern Highlands", "East New Britain", "West New Britain",
    "New Ireland", "Milne Bay", "East Sepik", "West Sepik", "Bougainville", "Hela", "Enga",
    "Jiwaka", "Simbu", "Chimbu", "Morobe", "Madang", "Manus", "Oro", "Gulf", "Central",
    "Western", "Sandaun",
)
_PNG_ALIASES = {
    "Port Moresby": "National Capital District", "NCD": "National Capital District",
    "Lae": "Morobe", "Mount Hagen": "Western Highlands", "Goroka": "Eastern Highlands",
    "Kainantu": "Eastern Highlands", "Kokopo": "East New Britain", "Rabaul": "East New Britain",
    "Kimbe": "West New Britain", "Tabubil": "Western", "Kiunga": "Western", "Porgera": "Enga",
    "Wabag": "Enga", "Lihir": "New Ireland", "Kavieng": "New Ireland", "Wewak": "East Sepik",
    "Alotau": "Milne Bay", "Popondetta": "Oro", "Kerema": "Gulf", "Tari": "Hela",
    "Mendi": "Southern Highlands", "Arawa": "Bougainville", "Buka": "Bougainville",
    "Chimbu": "Simbu", "Autonomous Region of Bougainville": "Bougainville", "Sandaun": "West Sepik",
}


@dataclass
class Section:
    heading: str
    body: str
    source: str = "generated"


@dataclass
class Record:
    """One relevant signal, reduced to what the report reads."""

    region: str
    sector: str
    company: str | None
    category: str
    source_name: str
    source_type: str
    title: str
    detail: str
    month: str
    day: str
    tier: str | None
    is_new: bool
    role: str | None
    state: str | None
    province: str | None
    agency: bool
    raw: str


@dataclass
class QuarterData:
    """Everything the prose is allowed to draw on, counted once."""

    quarter: str
    total: int = 0
    window_from: str | None = None
    window_to: str | None = None
    #: Classified outside the five sectors and left out of every figure, the
    #: same rule the weekly digest and the dashboard follow.
    not_relevant: int = 0
    by_sector: Counter[str] = field(default_factory=Counter)
    by_region: Counter[str] = field(default_factory=Counter)
    by_source: Counter[str] = field(default_factory=Counter)
    by_source_type: Counter[str] = field(default_factory=Counter)
    by_category: Counter[str] = field(default_factory=Counter)
    by_month: Counter[str] = field(default_factory=Counter)
    #: (region, sector) -> signal count, including rows with no named employer.
    by_region_sector: Counter[tuple[str, str]] = field(default_factory=Counter)
    #: (region, sector) -> company -> count
    companies: dict[tuple[str, str], Counter[str]] = field(default_factory=dict)
    #: (region, sector) -> role -> count
    roles: dict[tuple[str, str], Counter[str]] = field(default_factory=dict)
    roles_overall: Counter[str] = field(default_factory=Counter)
    records: list[Record] = field(default_factory=list)
    #: Watchlist companies per tier, for "how much of the list appeared".
    watchlist_by_tier: Counter[str] = field(default_factory=Counter)
    #: The quarter before, counted the same way, for comparisons. None when it
    #: held no signals: a change from nothing is not a trend.
    previous: "QuarterData | None" = None

    @property
    def months(self) -> list[str]:
        start, _end = quarter_bounds(self.quarter)
        return [f"{start.year}-{start.month + i:02d}" for i in range(3)]


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


def previous_quarter(quarter: str) -> str:
    start, _ = quarter_bounds(quarter)
    year, q = start.year, (start.month - 1) // 3 + 1
    return f"{year - 1}-Q4" if q == 1 else f"{year}-Q{q - 1}"


# --------------------------------------------------------------------------
# Reading a signal
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


def _parts(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").split("|")]


def _state_in(header: str) -> str | None:
    """The Australian state a job ad names, or None when it names none."""
    for code in _STATE_CODES:
        if re.search(rf"(?<![A-Za-z]){code}(?![A-Za-z])", header):
            return code
    for name, code in sorted(_STATE_NAMES.items(), key=lambda kv: -len(kv[0])):
        if name.lower() in header.lower():
            return code
    for place, code in sorted(_AU_PLACES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(place)}\b", header, re.IGNORECASE):
            return code
    return None


def _province_in(header: str) -> str | None:
    """The PNG province a job ad names, or None when it names none."""
    for name in _PNG_PROVINCES:
        if re.search(rf"\b{re.escape(name)}\b", header, re.IGNORECASE):
            return _PNG_ALIASES.get(name, name)
    for place, province in sorted(_PNG_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(place)}\b", header, re.IGNORECASE):
            return province
    return None


# --------------------------------------------------------------------------
# Gathering
# --------------------------------------------------------------------------


def _count(quarter: str, target: str | Path | None, *, with_previous: bool) -> QuarterData:
    start, end = quarter_bounds(quarter)
    data = QuarterData(quarter=quarter)

    resolved = resolve_target(target)
    if not is_postgres(resolved) and not Path(resolved).exists():
        return data

    try:
        with connect(resolved, readonly=True) as conn:
            rows = conn.execute(
                "SELECT company_name, sector, geography, signal_category, source_name, "
                "source_type, raw_content, captured_at, watchlist_tier, is_new_prospect "
                "FROM signals WHERE classified_at IS NOT NULL "
                "AND captured_at >= ? AND captured_at < ? "
                "ORDER BY captured_at",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
            try:
                tiers = conn.execute(
                    "SELECT tier, count(*) AS n FROM watchlist GROUP BY tier").fetchall()
                data.watchlist_by_tier = Counter({str(t["tier"]): int(t["n"] or 0) for t in tiers})
            except Exception:  # noqa: BLE001 - no watchlist table: nothing to compare
                pass
    except Exception as exc:  # noqa: BLE001 - unreachable database, missing table
        log.warning("publish: could not read signals for %s (%s)", quarter, exc)
        return data

    for r in rows:
        raw = r["raw_content"] or ""
        sector = r["sector"] or "other"
        if sector not in RELEVANT_SECTORS:
            data.not_relevant += 1
            continue

        # Same rule the dashboard uses: the scraper's market is the baseline and
        # keywords may only promote a row to PNG.
        region = infer_geography(raw, default=(r["geography"] or "AU"))
        company = (r["company_name"] or "").strip()
        # "Unknown" is what the classifier writes when it could not name the
        # employer; naming it in a client report would be embarrassing.
        named = company if company and company.lower() != "unknown" else None
        source_type = r["source_type"] or "job_board"
        parts = _parts(raw)
        header = " | ".join(parts[:4])
        captured = str(r["captured_at"] or "")
        role = _role_in(raw)

        rec = Record(
            region=region, sector=sector, company=named,
            category=r["signal_category"] or "hiring_velocity",
            source_name=r["source_name"] or "unknown", source_type=source_type,
            title=parts[0] if parts else raw[:120],
            detail=parts[1] if len(parts) > 1 else "",
            month=captured[:7], day=captured[:10],
            tier=(str(r["watchlist_tier"]) if r["watchlist_tier"] else None),
            is_new=bool(int(r["is_new_prospect"] or 0)),
            role=role,
            state=_state_in(header) if region == "AU" and source_type == "job_board" else None,
            province=_province_in(header) if region == "PNG" else None,
            agency=bool(named and is_agency(named)),
            raw=raw,
        )
        data.records.append(rec)

        data.total += 1
        data.by_sector[sector] += 1
        data.by_region[region] += 1
        data.by_source[rec.source_name] += 1
        data.by_source_type[source_type] += 1
        data.by_category[rec.category] += 1
        data.by_month[rec.month] += 1

        key = (region, sector)
        data.by_region_sector[key] += 1
        if named:
            data.companies.setdefault(key, Counter())[named] += 1
        if role:
            data.roles.setdefault(key, Counter())[role] += 1
            data.roles_overall[role] += 1

    if data.records:
        data.window_from = str(rows[0]["captured_at"])
        data.window_to = str(rows[-1]["captured_at"])

    if with_previous:
        prev = _count(previous_quarter(quarter), resolved, with_previous=False)
        data.previous = prev if prev.total else None

    log.info("publish.gather: %s -> %d signals (%d outside the sectors left out)",
             quarter, data.total, data.not_relevant)
    return data


def gather(quarter: str, target: str | Path | None = None) -> QuarterData:
    """Count everything the report needs, and the quarter before for comparison."""
    return _count(quarter, target, with_previous=True)


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


def _pct(part: int, whole: int) -> str:
    return f"{round(part / whole * 100)}%" if whole else "—"


def _enough(data: QuarterData) -> bool:
    return data.total >= MIN_SIGNALS_FOR_PERCENTAGES


def _share(data: QuarterData, part: int, whole: int | None = None) -> str:
    """A share, or a dash when the quarter is too thin to express one."""
    return _pct(part, whole if whole is not None else data.total) if _enough(data) else "—"


def _change(now: int, before: int | None) -> str:
    """Change on the previous quarter, or a dash when there is none to compare."""
    if not before:
        return "—"
    diff = round((now - before) / before * 100)
    return f"{'+' if diff > 0 else ''}{diff}%"


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


def _cell(value: Any) -> str:
    return str(value).replace("|", "/").replace("\n", " ").strip() or "—"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    """A "|" table the app and both exports render."""
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


def _pretty(sector: str) -> str:
    return SECTOR_PRETTY.get(sector, sector.replace("_", " ").title())


def _month_label(ym: str) -> str:
    try:
        return datetime.strptime(ym, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return ym


def _day_label(day: str) -> str:
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%d %b")
    except ValueError:
        return day


def _relationship(tier: str | None, is_new: bool, agency: bool) -> str:
    if agency:
        return "Recruitment agency"
    if tier:
        return f"Tier {tier} client"
    return "New prospect" if is_new else "—"


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def _key_figures(data: QuarterData) -> Section:
    prev = data.previous
    named = {r.company for r in data.records if r.company and not r.agency}
    prev_named = {r.company for r in prev.records if r.company and not r.agency} if prev else set()
    prospects = {r.company for r in data.records if r.is_new and r.company}
    clients = {r.company for r in data.records if r.tier and r.company}

    def row(label: str, now: int, before: int | None) -> list[Any]:
        return [label, now, before if before is not None else "—", _change(now, before)]

    p = prev
    rows = [
        row("Signals analysed", data.total, p.total if p else None),
        row("Job ads", data.by_source_type.get("job_board", 0),
            p.by_source_type.get("job_board", 0) if p else None),
        row("News items", data.by_source_type.get("news", 0),
            p.by_source_type.get("news", 0) if p else None),
        row("Tenders", data.by_source_type.get("tender", 0),
            p.by_source_type.get("tender", 0) if p else None),
        row("Australia", data.by_region.get("AU", 0), p.by_region.get("AU", 0) if p else None),
        row("Papua New Guinea", data.by_region.get("PNG", 0), p.by_region.get("PNG", 0) if p else None),
        row("Named employers", len(named), len(prev_named) if p else None),
        row("Watchlist clients active", len(clients),
            len({r.company for r in p.records if r.tier and r.company}) if p else None),
        row("New prospects", len(prospects),
            len({r.company for r in p.records if r.is_new and r.company}) if p else None),
    ]
    compare = (f"compared with {p.quarter}" if p else
               "with no earlier quarter on record to compare against")
    body = (f"{data.quarter} at a glance, {compare}.\n\n"
            + _table(["Measure", data.quarter, p.quarter if p else "Previous quarter", "Change"], rows))
    return Section("Key Figures", body)


def _executive_summary(data: QuarterData) -> Section:
    if data.total == 0:
        return Section("Executive Summary", (
            f"No signals were collected during {data.quarter}. This report has no "
            "findings to present. Run the collection pipeline and regenerate."))

    au, png = data.by_region.get("AU", 0), data.by_region.get("PNG", 0)
    bits = [
        f"MIOS collected and classified {data.total} hiring signals during "
        f"{data.quarter}, drawn from {len(data.by_source)} sources across "
        f"Australia and Papua New Guinea."
    ]
    if _enough(data):
        top_sector, top_n = data.by_sector.most_common(1)[0]
        bits.append(
            f"{_pretty(top_sector)} accounted for {_pct(top_n, data.total)} of activity, "
            "the largest share of any sector.")
        bits.append(f"Australia contributed {_pct(au, data.total)} of signals and "
                    f"Papua New Guinea {_pct(png, data.total)}.")
    else:
        bits.append(f"Australia contributed {au} and Papua New Guinea {png}. The volume "
                    "this quarter is too low to express as meaningful percentages.")
    if data.roles_overall:
        bits.append(f"The most frequently advertised roles were {_listing(data.roles_overall, 3)}.")

    findings: list[str] = []
    employers = Counter(r.company for r in data.records if r.company and not r.agency)
    if employers:
        top, n = employers.most_common(1)[0]
        findings.append(f"The most active employer was {top}, with {n} signal{'s' if n != 1 else ''}.")
    prospects = Counter(r.company for r in data.records if r.is_new and r.company)
    if prospects:
        findings.append(f"{len(prospects)} companies not on the watchlist were active in "
                        f"Easy Skill's sectors, led by {_listing(prospects, 3)}.")
    projects = sum(1 for r in data.records if r.category in ("project", "financial"))
    if projects:
        findings.append(f"{projects} signals concerned projects, investment or finance "
                        "rather than individual vacancies.")
    tenders = data.by_source_type.get("tender", 0)
    if tenders:
        findings.append(f"{tenders} government tenders in these sectors were published.")
    if data.previous and _enough(data) and _enough(data.previous):
        findings.append(f"Activity was {_change(data.total, data.previous.total)} on "
                        f"{data.previous.quarter} ({data.previous.total} signals).")
    agencies = sum(1 for r in data.records if r.agency)
    if agencies:
        findings.append(f"{agencies} job ads were placed by recruitment agencies rather "
                        "than the employer directly.")

    body = " ".join(bits)
    if findings:
        body += "\n\nKey findings:\n\n" + _bullets(findings)
    return Section("Executive Summary", body)


def _market_overview(data: QuarterData) -> Section:
    if data.total == 0:
        return Section("Market Overview", "No activity was recorded this quarter.")

    monthly = defaultdict(Counter)
    for r in data.records:
        monthly[r.month][r.source_type] += 1
    month_rows = []
    for m in data.months:
        c = monthly.get(m, Counter())
        month_rows.append([_month_label(m), c.get("job_board", 0), c.get("news", 0),
                           c.get("tender", 0), sum(c.values())])
    busiest = max(data.months, key=lambda m: data.by_month.get(m, 0))

    sector_rows = []
    for s in SECTOR_ORDER:
        au_n = data.by_region_sector.get(("AU", s), 0)
        png_n = data.by_region_sector.get(("PNG", s), 0)
        total = au_n + png_n
        prev_n = data.previous.by_sector.get(s, 0) if data.previous else None
        sector_rows.append([_pretty(s), au_n, png_n, total, _share(data, total),
                            _change(total, prev_n)])

    cat_rows = [[CATEGORY_LABEL.get(k, k), n, _share(data, n)]
                for k, n in data.by_category.most_common()]

    narrative = (
        f"Activity was spread across {data.quarter} as follows. The busiest month was "
        f"{_month_label(busiest)} with {data.by_month.get(busiest, 0)} signals. "
        f"Job ads made up {data.by_source_type.get('job_board', 0)} of the "
        f"{data.total} signals, industry news {data.by_source_type.get('news', 0)} and "
        f"government tenders {data.by_source_type.get('tender', 0)}.")
    body = "\n\n".join([
        narrative,
        "### Activity by month",
        _table(["Month", "Job ads", "News", "Tenders", "Total"], month_rows),
        "### Activity by sector and market",
        _table(["Sector", "Australia", "PNG", "Total", "Share", "Change on last quarter"],
               sector_rows),
        "### What kind of activity",
        _table(["Signal type", "Signals", "Share"], cat_rows),
    ])
    return Section("Market Overview", body)


def _sector_chapter(data: QuarterData, sector: str) -> Section:
    pretty = _pretty(sector)
    recs = [r for r in data.records if r.sector == sector]
    if not recs:
        return Section(pretty, (
            f"No {pretty.lower()} activity was detected in Australia or Papua New Guinea "
            "this quarter. That is an absence of signal in the sources MIOS monitors, not "
            "evidence that no hiring took place."))

    au_n = sum(1 for r in recs if r.region == "AU")
    png_n = len(recs) - au_n
    employers = Counter(r.company for r in recs if r.company)
    roles = Counter(r.role for r in recs if r.role)

    lines = [
        f"MIOS detected {len(recs)} hiring signal{'s' if len(recs) != 1 else ''} across "
        f"{len(employers)} {pretty.lower()} employer{'s' if len(employers) != 1 else ''} "
        f"during {data.quarter}: {au_n} in Australia and {png_n} in Papua New Guinea."
    ]
    if _enough(data):
        lines.append(f"That is {_pct(len(recs), data.total)} of all activity in the report.")
    if employers:
        lines.append(f"The most active were {_listing(employers)}.")
    if roles:
        lines.append(f"Demand concentrated in {_listing(roles, 4)}, counted by role title "
                     "across the postings collected.")
    parts = [" ".join(lines)]

    if employers:
        meta = {}
        for r in recs:
            if r.company:
                m = meta.setdefault(r.company, {"regions": set(), "tier": None, "new": False,
                                                "agency": r.agency})
                m["regions"].add("PNG" if r.region == "PNG" else "Australia")
                m["tier"] = m["tier"] or r.tier
                m["new"] = m["new"] or r.is_new
        rows = [[name, n, " + ".join(sorted(meta[name]["regions"])),
                 _relationship(meta[name]["tier"], meta[name]["new"], meta[name]["agency"])]
                for name, n in employers.most_common(TOP_EMPLOYERS)]
        parts += ["### Most active employers",
                  _table(["Employer", "Signals", "Market", "Relationship"], rows)]

    mix = Counter(r.category for r in recs)
    parts += ["### Signal mix",
              _table(["Signal type", "Signals"],
                     [[CATEGORY_LABEL.get(k, k), n] for k, n in mix.most_common()])]

    developments = [r for r in recs if r.source_type in ("news", "tender")
                    and r.category in ("project", "financial", "leadership", "competitive")]
    if developments:
        items = [f"{_day_label(r.day)} · {r.detail or r.source_name} · {r.title}"
                 for r in sorted(developments, key=lambda r: r.day, reverse=True)[:TOP_DEVELOPMENTS]]
        parts += ["### Notable developments", _bullets(items)]

    return Section(pretty, "\n\n".join(parts))


def _australia_by_state(data: QuarterData) -> Section:
    jobs = [r for r in data.records if r.region == "AU" and r.source_type == "job_board"]
    if not jobs:
        return Section("Australia by State", (
            "No Australian job ads were collected this quarter, so there is nothing to "
            "break down by state."))
    by_state: dict[str, list[Record]] = defaultdict(list)
    for r in jobs:
        by_state[r.state or "Not stated"].append(r)

    rows = []
    for state, recs in sorted(by_state.items(), key=lambda kv: (kv[0] == "Not stated", -len(kv[1]))):
        sectors = Counter(r.sector for r in recs)
        employers = Counter(r.company for r in recs if r.company)
        rows.append([_STATE_FULL.get(state, state), len(recs),
                     _pretty(sectors.most_common(1)[0][0]),
                     employers.most_common(1)[0][0] if employers else "—"])
    stated = sum(len(v) for k, v in by_state.items() if k != "Not stated")
    lead = max((k for k in by_state if k != "Not stated"), key=lambda k: len(by_state[k]),
               default=None)
    narrative = (
        f"Of {len(jobs)} Australian job ads, {stated} named a state or a town that places one. "
        + (f"{_STATE_FULL.get(lead, lead)} led with {len(by_state[lead])}." if lead else "")
    )
    return Section("Australia by State", "\n\n".join([
        narrative,
        _table(["State", "Job ads", "Leading sector", "Most active employer"], rows),
        "Placed from the location each advert gives. Ads that name no state or recognised "
        "town are counted as not stated rather than guessed.",
    ]))


def _png_section(data: QuarterData) -> Section:
    png_total = data.by_region.get("PNG", 0)
    if not png_total:
        return Section("Papua New Guinea", (
            "No Papua New Guinea signals were collected this quarter. PNG coverage "
            "depends on PNGworkforce, PNG Business News and Business Advantage PNG; check "
            "they are reachable before reading this as a market slowdown."))

    png_companies: Counter[str] = Counter()
    png_roles: Counter[str] = Counter()
    for (region, _sector), counter in data.companies.items():
        if region == "PNG":
            png_companies.update(counter)
    for (region, _sector), counter in data.roles.items():
        if region == "PNG":
            png_roles.update(counter)
    png_sectors: Counter[str] = Counter()
    for (region, sector), count in data.by_region_sector.items():
        if region == "PNG":
            png_sectors[sector] += count

    parts = [
        f"Papua New Guinea produced {png_total} signals this quarter across "
        f"{len(png_companies)} identified employers."
    ]
    if png_sectors:
        named = ", ".join(f"{_pretty(s)} ({n})" for s, n in png_sectors.most_common(4))
        parts.append(f"By sector: {named}.")
    if png_companies:
        parts.append(f"The most active employers were {_listing(png_companies)}.")
    if png_roles:
        parts.append(f"Role demand centred on {_listing(png_roles, 4)}.")
    blocks = [" ".join(parts)]

    recs = [r for r in data.records if r.region == "PNG"]
    by_province: dict[str, list[Record]] = defaultdict(list)
    for r in recs:
        if r.source_type == "job_board":
            by_province[r.province or "Not stated"].append(r)
    if by_province:
        rows = []
        for prov, rs in sorted(by_province.items(),
                               key=lambda kv: (kv[0] == "Not stated", -len(kv[1]))):
            emp = Counter(r.company for r in rs if r.company)
            rows.append([prov, len(rs), _pretty(Counter(r.sector for r in rs).most_common(1)[0][0]),
                         emp.most_common(1)[0][0] if emp else "—"])
        blocks += ["### Job ads by province",
                   _table(["Province", "Job ads", "Leading sector", "Most active employer"], rows)]
    return Section("Papua New Guinea", "\n\n".join(blocks))


def _employer_activity(data: QuarterData) -> Section:
    named = [r for r in data.records if r.company and not r.agency]
    if not named:
        return Section("Employer Activity", "No employer could be named from this quarter's signals.")
    counts = Counter(r.company for r in named)
    info: dict[str, dict[str, Any]] = {}
    for r in named:
        i = info.setdefault(r.company, {"sectors": Counter(), "regions": set(), "tier": None,
                                        "new": False, "months": Counter()})
        i["sectors"][r.sector] += 1
        i["regions"].add("PNG" if r.region == "PNG" else "AU")
        i["tier"] = i["tier"] or r.tier
        i["new"] = i["new"] or r.is_new
        i["months"][r.month] += 1

    rows = [[name, n, _pretty(info[name]["sectors"].most_common(1)[0][0]),
             " + ".join(sorted(info[name]["regions"])),
             _relationship(info[name]["tier"], info[name]["new"], False)]
            for name, n in counts.most_common(TOP_EMPLOYERS_OVERALL)]

    first, last = data.months[0], data.months[-1]
    rising = [(name, info[name]["months"].get(first, 0), info[name]["months"].get(last, 0))
              for name, n in counts.items()
              if n >= 3 and info[name]["months"].get(last, 0) > info[name]["months"].get(first, 0)]
    rising.sort(key=lambda t: (t[1] - t[2], t[0]))

    tier_rows = []
    for tier in ("A", "B", "C"):
        seen = {r.company for r in named if r.tier == tier}
        on_list = data.watchlist_by_tier.get(tier, 0)
        tier_rows.append([f"Tier {tier}", len(seen), on_list or "—",
                          _pct(len(seen), on_list) if on_list else "—"])

    blocks = [
        f"{len(counts)} employers were named across the quarter's signals, excluding "
        f"recruitment agencies. The twenty most active were:",
        _table(["Employer", "Signals", "Main sector", "Market", "Relationship"], rows),
        "### Watchlist clients seen this quarter",
        _table(["Tier", "Seen", "On the watchlist", "Share seen"], tier_rows),
    ]
    if rising:
        blocks += [
            "### Rising activity",
            f"Employers with at least three signals whose activity in {_month_label(last)} "
            f"exceeded {_month_label(first)}:",
            _table(["Employer", _month_label(first), _month_label(last)],
                   [[n, a, b] for n, a, b in rising[:10]]),
        ]
    return Section("Employer Activity", "\n\n".join(blocks))


def _new_prospects(data: QuarterData) -> Section:
    prospects = [r for r in data.records
                 if r.is_new and r.company and not r.agency and r.source_type != "tender"]
    if not prospects:
        return Section("New Prospects", (
            "No company outside the watchlist was active in Easy Skill's sectors this "
            "quarter, or none could be named."))
    counts = Counter(r.company for r in prospects)
    info: dict[str, dict[str, Any]] = {}
    for r in prospects:
        i = info.setdefault(r.company, {"sector": Counter(), "region": set(), "first": r.day})
        i["sector"][r.sector] += 1
        i["region"].add("PNG" if r.region == "PNG" else "AU")
        i["first"] = min(i["first"], r.day)
    rows = [[name, n, _pretty(info[name]["sector"].most_common(1)[0][0]),
             " + ".join(sorted(info[name]["region"])), _day_label(info[name]["first"])]
            for name, n in counts.most_common(TOP_EMPLOYERS_OVERALL)]
    return Section("New Prospects", "\n\n".join([
        f"{len(counts)} companies not on the watchlist were active in mining, oil and gas, "
        "construction, defence or energy transition. Recruitment agencies, tender buyers "
        "and companies outside those sectors are excluded. The most active were:",
        _table(["Company", "Signals", "Sector", "Market", "First seen"], rows),
    ]))


def _skills(data: QuarterData) -> Section:
    if not data.roles_overall:
        return Section("Skills Demand", "No recognised role titles were detected in this quarter's postings.")
    ranked = data.roles_overall.most_common(10)
    table = "\n".join(f"- {role.title()} — {n} posting{'s' if n != 1 else ''}" for role, n in ranked)
    matched = sum(data.roles_overall.values())
    body = (
        f"Role demand across both markets, counted by title across "
        f"{matched} of {data.total} postings:\n\n{table}\n\n"
        "Postings whose title matched none of the tracked disciplines are "
        "excluded rather than bucketed as 'other', so the counts above are "
        "understated rather than padded."
    )
    by_sector: dict[str, Counter[str]] = defaultdict(Counter)
    for r in data.records:
        if r.role:
            by_sector[r.role][r.sector] += 1
    rows = [[role.title()] + [by_sector[role].get(s, 0) for s in SECTOR_ORDER] + [n]
            for role, n in ranked]
    body += "\n\n### Roles by sector\n\n" + _table(
        ["Role"] + [_pretty(s) for s in SECTOR_ORDER] + ["Total"], rows)
    return Section("Skills Demand", body)


def _leadership(data: QuarterData) -> Section:
    recs = sorted((r for r in data.records if r.category == "leadership"),
                  key=lambda r: r.day, reverse=True)
    if not recs:
        return Section("Leadership Changes", "No senior appointments or leadership roles were detected this quarter.")
    items = [f"{_day_label(r.day)} · {r.company or r.detail or 'Unnamed'} · {r.title}"
             for r in recs[:15]]
    return Section("Leadership Changes", "\n\n".join([
        f"{len(recs)} signals concerned senior appointments or leadership roles — often an "
        "early sign of a team being built or restructured.",
        _bullets(items),
    ]))


def _projects_and_tenders(data: QuarterData) -> Section:
    projects = sorted((r for r in data.records if r.source_type == "news"
                       and r.category in ("project", "financial")),
                      key=lambda r: r.day, reverse=True)
    tenders = sorted((r for r in data.records if r.source_type == "tender"),
                     key=lambda r: r.day, reverse=True)
    if not projects and not tenders:
        return Section("Projects, Investment and Tenders",
                       "No project, investment or tender activity was recorded this quarter.")
    blocks = [
        f"{len(projects)} news items concerned projects, investment or finance, and "
        f"{len(tenders)} government tenders were published in these sectors."
    ]
    if projects:
        by_sector = Counter(r.sector for r in projects)
        blocks += [
            "### Project and investment news",
            "By sector: " + ", ".join(f"{_pretty(s)} ({n})" for s, n in by_sector.most_common()) + ".",
            _bullets([f"{_day_label(r.day)} · {r.detail or r.source_name} · {r.title}"
                      for r in projects[:15]]),
        ]
    if tenders:
        rows = []
        for r in tenders[:20]:
            p = _parts(r.raw)
            closes = next((x for x in p if x.lower().startswith("closes")), "—")
            rows.append([_day_label(r.day), p[1] if len(p) > 1 else "—",
                         r.title[:110], closes.replace("closes", "").strip() or "—"])
        blocks += ["### Government tenders",
                   _table(["Published", "Agency", "Description", "Closes"], rows)]
    return Section("Projects, Investment and Tenders", "\n\n".join(blocks))


def _competitors(data: QuarterData) -> Section:
    jobs = [r for r in data.records if r.source_type == "job_board"]
    agency_ads = [r for r in jobs if r.agency]
    if not agency_ads:
        return Section("Competitor Activity",
                       "No job ads from recruitment or labour-hire agencies were identified this quarter.")
    counts = Counter(r.company for r in agency_ads)
    sectors: dict[str, Counter[str]] = defaultdict(Counter)
    for r in agency_ads:
        sectors[r.company][r.sector] += 1
    rows = [[name, n, _pretty(sectors[name].most_common(1)[0][0])]
            for name, n in counts.most_common(TOP_EMPLOYERS)]
    share = f" ({_pct(len(agency_ads), len(jobs))} of job ads)" if len(jobs) >= MIN_SIGNALS_FOR_PERCENTAGES else ""
    return Section("Competitor Activity", "\n\n".join([
        f"{len(agency_ads)} job ads were placed by recruitment or labour-hire agencies{share}. "
        "These are competitors' placements, and the employer behind them is usually not "
        "named — which is why they are never offered as prospects.",
        _table(["Agency", "Job ads", "Main sector"], rows),
    ]))


def _methodology(data: QuarterData) -> Section:
    sources = ", ".join(f"{name} ({n})" for name, n in data.by_source.most_common())
    method = (
        f"Findings are drawn from {data.total} job postings, news items and tenders collected "
        f"automatically during {data.quarter} and classified by a language model into "
        "sector, region and signal type. "
        f"Sources this quarter: {sources or 'none'}. "
        "Counts describe advertised activity detected in those sources — they are a "
        "sample of the market, not a census of it, and a company absent from this "
        "report may simply hire through channels MIOS does not monitor. "
        "No figure in this report is estimated or extrapolated."
    )
    notes = [
        f"Signals classified outside mining, oil and gas, construction, defence and energy "
        f"transition are left out of every figure: {data.not_relevant} this quarter.",
        "Market is the one the source covers, promoted to Papua New Guinea where the text "
        "names a PNG location. States and provinces come from the location each job ad gives.",
        "Roles are counted by matching job titles against the disciplines Easy Skill "
        "tracks; unmatched titles are left out rather than guessed.",
        "New prospects are named companies in the five sectors that are not on the watchlist, "
        "excluding recruitment agencies and the agencies issuing tenders.",
        "Changes on the previous quarter are shown only where that quarter holds data.",
    ]
    return Section("Methodology", method + "\n\n" + _bullets(notes))


def _appendix_months(data: QuarterData) -> Section:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for r in data.records:
        counts[r.sector][r.month] += 1
    rows = [[_pretty(s)] + [counts[s].get(m, 0) for m in data.months] + [sum(counts[s].values())]
            for s in SECTOR_ORDER]
    rows.append(["All sectors"] + [data.by_month.get(m, 0) for m in data.months] + [data.total])
    return Section("Appendix A — Signals by Sector and Month", _table(
        ["Sector"] + [_month_label(m) for m in data.months] + ["Total"], rows))


def _appendix_employers(data: QuarterData) -> Section:
    counts = Counter(r.company for r in data.records if r.company)
    listed = [(name, n) for name, n in counts.most_common() if n >= APPENDIX_MIN_SIGNALS]
    if not listed:
        return Section("Appendix B — Employers", "No employer appeared more than once this quarter.")
    sectors: dict[str, Counter[str]] = defaultdict(Counter)
    regions: dict[str, set[str]] = defaultdict(set)
    for r in data.records:
        if r.company:
            sectors[r.company][r.sector] += 1
            regions[r.company].add("PNG" if r.region == "PNG" else "AU")
    rows = [[name, n, _pretty(sectors[name].most_common(1)[0][0]), " + ".join(sorted(regions[name]))]
            for name, n in listed]
    return Section("Appendix B — Employers", "\n\n".join([
        f"Every employer with at least {APPENDIX_MIN_SIGNALS} signals this quarter.",
        _table(["Employer", "Signals", "Main sector", "Market"], rows),
    ]))


def _appendix_sources(data: QuarterData) -> Section:
    kinds: dict[str, str] = {}
    for r in data.records:
        kinds[r.source_name] = SOURCE_TYPE_LABEL.get(r.source_type, r.source_type)
    rows = [[name, kinds.get(name, "—"), n, _share(data, n)] for name, n in data.by_source.most_common()]
    return Section("Appendix C — Sources", _table(["Source", "Type", "Signals", "Share"], rows))


def build_sections(data: QuarterData) -> list[Section]:
    """The report, section by section, in reading order."""
    sections: list[Section] = [
        _key_figures(data),
        _executive_summary(data),
        _market_overview(data),
    ]
    sections += [_sector_chapter(data, s) for s in SECTOR_ORDER]
    sections += [
        _australia_by_state(data),
        _png_section(data),
        _employer_activity(data),
        _new_prospects(data),
        _skills(data),
        _leadership(data),
        _projects_and_tenders(data),
        _competitors(data),
        # Human judgement, deliberately empty.
        Section("Looking Ahead", "", source="manual"),
        _methodology(data),
        _appendix_months(data),
        _appendix_employers(data),
        _appendix_sources(data),
    ]
    return sections


def generate(quarter: str, target: str | Path | None = None) -> tuple[QuarterData, list[Section]]:
    """Gather and write in one call — what the API endpoint uses."""
    data = gather(quarter, target)
    return data, build_sections(data)
