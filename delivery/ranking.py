"""Which signals a digest shows, and in what order.

One ranking for both places a digest is read: the Weekly digest page, which
shows forty, and the Slack message, which shows ten.

**What it replaced.** Signals took turns: one from each region, and within a
region one from each source, each source's own list sorted by category, then
watchlist tier, then by how long the collected text was. That was built for
three sources and stopped making sense at eighteen. A source's single weak item
took a slot ahead of another source's second strong one; a project story about
a company nobody knows ranked above a Tier A client hiring; a company with
twelve vacancies filled twelve rows; and the last tiebreak, the length of an
advert, said nothing about whether it mattered.

**How it works now.**

1. *Fold.* A company's job ads in one market become one line, "12 roles
   advertised", with the roles listed inside it. Twelve ads are one fact about
   a company, not twelve. News and tenders are never folded: each is its own
   story.
2. *Score.* Every line gets a score out of 100, the sum of three parts that are
   written down below and shown to the reader:
   who it is about (the watchlist tier), what kind of signal it is, and how
   much else was collected about the same company in the same digest.
3. *Select.* The strongest lines are taken in score order, with two limits:
   no company takes more than a few lines, and each market is guaranteed a
   share, so a busy week in Australia cannot push Papua New Guinea off the
   page. Ties go to the more recently collected.

The score is a ranking aid made of counted facts, not a probability. It
replaces a number the page used to call "conf", which was derived from the
row's position on the screen and measured nothing.

Everything here is pure: no database, no clock. `rules()` returns the weights
in the form the page guide prints, so the explanation cannot drift from the
code.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

#: Markets, in display order. Each is guaranteed REGION_SHARE of a digest.
REGION_ORDER = ("AU", "PNG")

# --------------------------------------------------------------------------
# The weights
# --------------------------------------------------------------------------

#: Who the signal is about. The largest part, because the same news matters
#: more about a client than about a stranger.
TIER_POINTS = {"A": 50, "B": 40, "C": 30}
#: A named company in one of the five sectors that is not on the watchlist.
PROSPECT_POINTS = 18
#: Any other named company (an agency, a government body).
NAMED_POINTS = 8

#: What kind of signal it is. A change of leadership or a new project says more
#: about where hiring is going than one more routine vacancy.
CATEGORY_POINTS = {
    "leadership": 30,
    "project": 26,
    "financial": 18,
    "competitive": 16,
    "hiring_velocity": 12,
    "market_intel": 6,
}
CATEGORY_LABEL = {
    "leadership": "Leadership change",
    "project": "Project or tender",
    "financial": "Financial news",
    "competitive": "Competitor move",
    "hiring_velocity": "Hiring",
    "market_intel": "Market news",
}

#: How much else this digest holds about the same company: (at least this many
#: signals, points). One signal on its own earns nothing here.
ACTIVITY_STEPS = ((10, 20), (5, 16), (3, 11), (2, 6))

#: Lines one company may take in a digest.
MAX_PER_COMPANY = 3
#: The share of a digest each market is guaranteed, when it has that much.
REGION_SHARE = 0.3
#: Roles listed inside a folded line. The count is always the true number.
MAX_ROLES_LISTED = 30


# --------------------------------------------------------------------------
# Shapes
# --------------------------------------------------------------------------


@dataclass
class Item:
    """One classified signal, as much of it as ranking needs."""

    key: str
    #: "" when the classifier could not name anyone.
    company: str
    region: str
    category: str
    #: job_board, news or tender.
    kind: str
    tier: str | None
    is_new: bool
    #: ISO-8601 UTC, which sorts as text.
    captured_at: str
    title: str
    #: The caller's own row, handed back untouched.
    ref: Any = None


@dataclass
class Line:
    """What a reader sees as one row: a signal, or a company's folded job ads."""

    items: list[Item]
    score: int = 0
    #: The working, as (label, points), in the order the guide explains it.
    parts: list[tuple[str, int]] = field(default_factory=list)

    @property
    def lead(self) -> Item:
        return self.items[0]

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def folded(self) -> bool:
        return len(self.items) > 1


def _company_key(name: str) -> str:
    return (name or "").strip().casefold()


# --------------------------------------------------------------------------
# 1. Fold
# --------------------------------------------------------------------------


def fold(items: list[Item]) -> list[Line]:
    """One line per company's job ads in a market; everything else on its own."""
    lines: list[Line] = []
    groups: dict[tuple[str, str], Line] = {}
    for item in items:
        foldable = (item.kind == "job_board" and item.category == "hiring_velocity"
                    and _company_key(item.company))
        if not foldable:
            lines.append(Line([item]))
            continue
        key = (_company_key(item.company), item.region)
        line = groups.get(key)
        if line is None:
            line = groups[key] = Line([item])
            lines.append(line)
        else:
            line.items.append(item)
    for line in lines:
        # The newest ad leads, so the line's date and link are the latest.
        line.items.sort(key=lambda i: i.key)
        line.items.sort(key=lambda i: i.captured_at, reverse=True)
    return lines


# --------------------------------------------------------------------------
# 2. Score
# --------------------------------------------------------------------------


def standing(tier: str | None, is_new: bool, company: str) -> tuple[str, int]:
    tier = (tier or "").upper()
    if tier in TIER_POINTS:
        return f"Tier {tier} client", TIER_POINTS[tier]
    if not _company_key(company):
        return "No company named", 0
    if is_new:
        return "New prospect", PROSPECT_POINTS
    return "Other company", NAMED_POINTS


def activity(n: int) -> int:
    for at_least, points in ACTIVITY_STEPS:
        if n >= at_least:
            return points
    return 0


def score(line: Line, signals_about: Counter[str]) -> None:
    """Fill in a line's score and the working behind it."""
    lead = line.lead
    who, who_points = standing(lead.tier, lead.is_new, lead.company)
    what_points = CATEGORY_POINTS.get(lead.category, 0)
    n = signals_about.get(_company_key(lead.company), 0) if _company_key(lead.company) else 0
    more_points = activity(n)
    line.parts = [
        (who, who_points),
        (CATEGORY_LABEL.get(lead.category, "Signal"), what_points),
        (f"{n} signals about this company" if n > 1 else "Only signal about this company",
         more_points),
    ]
    line.score = who_points + what_points + more_points


# --------------------------------------------------------------------------
# 3. Select
# --------------------------------------------------------------------------


def select(ordered: list[Line], limit: int, *, per_company: int = MAX_PER_COMPANY,
           share: float = REGION_SHARE, regions: tuple[str, ...] = REGION_ORDER) -> list[Line]:
    """Take up to `limit` lines from a list already in ranked order."""
    if limit <= 0:
        return []
    chosen: set[int] = set()
    taken: Counter[str] = Counter()

    def fits(line: Line) -> bool:
        company = _company_key(line.lead.company)
        return not company or taken[company] < per_company

    def take(i: int) -> None:
        chosen.add(i)
        company = _company_key(ordered[i].lead.company)
        if company:
            taken[company] += 1

    # Each market's guaranteed share first, its strongest lines.
    floor = int(limit * share)
    for region in regions:
        got = 0
        for i, line in enumerate(ordered):
            if got >= floor or len(chosen) >= limit:
                break
            if i not in chosen and line.lead.region == region and fits(line):
                take(i)
                got += 1

    # Then the strongest of whatever is left, whichever market it is from.
    for i, line in enumerate(ordered):
        if len(chosen) >= limit:
            break
        if i not in chosen and fits(line):
            take(i)

    # A short digest is worse than a repetitive one: if the per-company limit
    # left rows unfilled, it gives way.
    for i in range(len(ordered)):
        if len(chosen) >= limit:
            break
        if i not in chosen:
            take(i)

    return [line for i, line in enumerate(ordered) if i in chosen]


def rank(items: list[Item], limit: int) -> list[Line]:
    """Fold, score and select. The lines come back strongest first."""
    lines = fold(items)
    signals_about: Counter[str] = Counter(
        _company_key(i.company) for i in items if _company_key(i.company))
    for line in lines:
        score(line, signals_about)
    # Three stable sorts, least important first: a fixed order for exact ties,
    # then the more recently collected, then the score.
    lines.sort(key=lambda ln: ln.lead.key)
    lines.sort(key=lambda ln: ln.lead.captured_at, reverse=True)
    lines.sort(key=lambda ln: ln.score, reverse=True)
    return select(lines, limit)


def roles_summary(line: Line, shown: int = 3) -> str:
    """A folded line said in a sentence: the first few roles, and how many more."""
    titles: list[str] = []
    for item in line.items:
        if item.title and item.title not in titles:
            titles.append(item.title)
    head = ", ".join(titles[:shown])
    more = line.count - min(shown, len(titles))
    if more > 0:
        return f"{head} and {more} more."
    return f"{head}."


# --------------------------------------------------------------------------
# For the page guide
# --------------------------------------------------------------------------


def rules() -> dict[str, Any]:
    """The weights, as the guide prints them."""
    return {
        "max": max(TIER_POINTS.values()) + max(CATEGORY_POINTS.values()) + ACTIVITY_STEPS[0][1],
        "who": [
            *({"label": f"Tier {t} client", "points": p} for t, p in TIER_POINTS.items()),
            {"label": "New prospect (not on the watchlist)", "points": PROSPECT_POINTS},
            {"label": "Any other named company", "points": NAMED_POINTS},
            {"label": "No company named", "points": 0},
        ],
        "what": [{"label": CATEGORY_LABEL[c], "points": p} for c, p in CATEGORY_POINTS.items()],
        "more": [
            *({"label": f"{n} or more signals about the company", "points": p}
              for n, p in reversed(ACTIVITY_STEPS)),
        ],
        "perCompany": MAX_PER_COMPANY,
        "regionShare": round(REGION_SHARE * 100),
        "regions": list(REGION_ORDER),
    }
