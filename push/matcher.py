"""Score companies against a candidate profile, using Mode Monitor's signals.

This is the heart of Mode Push: the BD team has a consultant, and needs to know
which companies to approach *now*, with a reason they can put in an email.

Deterministic on purpose. Every point in a score traces to a named contributor,
and each contributor produces an evidence line — the bullets the BD team reads
before deciding to make contact. An LLM score would be neither reproducible nor
explainable, and "94% match" with no reason is not something a consultant can
act on.

Scoring, out of 100:

    28  role demand    — is this company hiring for the candidate's discipline?
    14  skills overlap — do the candidate's skills appear in the roles?
    11  signal quality — what kind of signal is this, not just how many?
    11  sector fit     — does the candidate's sector match the company's?
     9  momentum       — is their hiring accelerating against their own baseline?
     7  hiring volume  — how much are they hiring right now?
     7  relationship   — existing watchlist client vs a new name
     5  seniority fit  — does the candidate's experience match the roles' level?
     4  region fit     — same market as the candidate
     4  recency        — how fresh the signals are

The four in the middle were added in v2. Each reads something the pipeline was
already collecting and the scorer ignored:

* **Skills** were parsed from the CV, shown in the UI, and never compared with
  anything. They are the most specific fit evidence held about a candidate.
* **Signal quality** distinguishes a company opening a project or changing its
  leadership from one running routine vacancies. Six ordinary postings and six
  postings around a new mine are not the same buying moment, and counting them
  identically was the largest thing the old model could not see.
* **Momentum** asks whether hiring is accelerating against that company's own
  history, which is the difference between a good account and a good *week* to
  call one.
* **Seniority** stops a graduate and a twenty-year planner scoring alike on the
  same job title.

Weights are constants below rather than being buried in the code, so they can be
tuned once the BD team has used it and can say what actually predicts a
placement. They are not calibrated against outcomes — nobody has placed anyone
through this yet — so they encode judgement, and the breakdown is returned with
every result precisely so that judgement can be argued with.

Separately from the score, each result carries a **confidence**: how much
evidence stands behind it. Three signals from one week and forty across a month
can produce the same number, and a consultant deciding whether to call should be
able to tell those apart.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import re

from rapidfuzz import fuzz

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Weights. These sum to 100.
# --------------------------------------------------------------------------

W_ROLE = 28
W_SKILLS = 14
W_SIGNAL_QUALITY = 11
W_SECTOR = 11
W_MOMENTUM = 9
W_VOLUME = 7
W_RELATIONSHIP = 7
W_SENIORITY = 5
W_REGION = 4
W_RECENCY = 4

#: What each kind of signal says about whether now is the moment to call.
#:
#: A new project or a leadership change is a decision point: budgets move and
#: teams get built. Routine vacancies say a company is ticking over. Competitive
#: and market intelligence describe the market rather than the company, so they
#: carry least — they are context for a conversation, not a reason to start one.
CATEGORY_WEIGHT: dict[str, float] = {
    "project": 1.0,
    "leadership": 0.9,
    "financial": 0.8,
    "hiring_velocity": 0.55,
    "market_intel": 0.3,
    "competitive": 0.3,
}
DEFAULT_CATEGORY_WEIGHT = 0.5

#: Words that place a role's seniority. Deliberately small and explicit: an
#: inferred ladder would be guesswork dressed as a measurement.
SENIORITY_MARKERS: tuple[tuple[str, int], ...] = (
    ("graduate", 0), ("trainee", 0), ("apprentice", 0), ("junior", 1),
    ("intermediate", 4), ("senior", 8), ("lead", 10), ("principal", 12),
    ("manager", 10), ("head of", 14), ("superintendent", 12), ("director", 16),
)

#: How far a candidate's experience may sit from a role's implied level before
#: the fit stops counting. Wide, because titles are a blunt instrument.
SENIORITY_TOLERANCE_YEARS = 6

#: A title this similar counts as the same discipline. rapidfuzz token_set_ratio
#: already handles word order, so this is about tolerating "Snr"/"Senior" and
#: "Maint." — not about matching unrelated roles.
ROLE_SIMILARITY_FLOOR = 62

#: Hiring volume that earns full marks. Beyond this the extra tells you little.
VOLUME_SATURATION = 6

#: Signals older than this contribute nothing to recency.
RECENCY_HORIZON_DAYS = 30

#: The window momentum treats as "now", and how many windows before it form the
#: baseline. A week against the previous three, matching the digest's own
#: velocity table so the two cannot tell different stories.
MOMENTUM_WINDOW_DAYS = 7
MOMENTUM_BASELINE_WINDOWS = 3

#: Growth that earns full marks. Doubling is decisive; beyond that the extra
#: says more about a small baseline than about the company.
MOMENTUM_SATURATION = 1.0

#: Below this baseline, the growth is stated in words rather than as a
#: percentage. See `_momentum` — the figure is arithmetically right and would
#: not survive a client asking where it came from.
MOMENTUM_MIN_BASELINE_TO_QUOTE = 1.0


@dataclass
class MatchResult:
    """One company, scored, with the reasoning that produced the score."""

    company: str
    score: int
    region: str | None
    sector: str | None
    tier: str | None
    is_new_prospect: bool
    signal_count: int
    #: How much evidence stands behind the score — "high", "medium" or "low".
    #: Reported beside it rather than folded into it: a thin case and a strong
    #: one can reach the same number, and that difference changes what a
    #: consultant should do next.
    confidence: str = "low"
    confidence_note: str = ""
    evidence: list[str] = field(default_factory=list)
    breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def relationship(self) -> str:
        return f"TIER {self.tier} · ACTIVE CLIENT" if self.tier else "NEW NAME"

    @property
    def recommended_action(self) -> str:
        # A warm client gets a candidate pitch; a new name needs an introduction
        # before a specific person is put forward.
        return "Send MPC email" if self.tier else "Cold outreach"

    def to_dict(self, rank: int) -> dict[str, Any]:
        return {
            "rank": rank,
            "co": self.company,
            "score": self.score,
            "rel": self.relationship,
            "region": self.region or "—",
            "sector": self.sector or "—",
            "evidence": self.evidence,
            "action": self.recommended_action,
            "signalCount": self.signal_count,
            "breakdown": self.breakdown,
            #: Beside the score, never folded into it.
            "confidence": self.confidence,
            "confidenceNote": self.confidence_note,
        }


# --------------------------------------------------------------------------
# Individual contributors
# --------------------------------------------------------------------------


def _parse_stamp(raw: Any) -> datetime | None:
    """A capture timestamp as an aware datetime, or None if unusable.

    Shared by recency and momentum so the two cannot disagree about what counts
    as a dated signal.
    """
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _role_demand(title: str | None, signals: list[dict]) -> tuple[int, str | None]:
    """How strongly this company is hiring for the candidate's discipline."""
    if not title:
        return 0, None
    best = 0
    best_text = ""
    for s in signals:
        content = (s.get("raw_content") or "")
        # Compare against the first segment: scrapers put the job title there.
        head = content.split("|", 1)[0].strip() or content[:80]
        score = fuzz.token_set_ratio(title.lower(), head.lower())
        if score > best:
            best, best_text = score, head[:70]
    if best < ROLE_SIMILARITY_FLOOR:
        return 0, None

    # Rescale: the floor earns nothing, a perfect title match earns full marks.
    scaled = round(W_ROLE * (best - ROLE_SIMILARITY_FLOOR) / (100 - ROLE_SIMILARITY_FLOOR))
    matching = sum(
        1 for s in signals
        if fuzz.token_set_ratio(
            title.lower(), (s.get("raw_content") or "").split("|", 1)[0].lower()
        ) >= ROLE_SIMILARITY_FLOOR
    )
    plural = "roles" if matching != 1 else "role"
    return scaled, f"{matching} {plural} matching “{title}” — closest: {best_text}"


def _sector_fit(sector: str | None, signals: list[dict]) -> tuple[int, str | None]:
    if not sector:
        return 0, None
    sectors = [s.get("sector") for s in signals if s.get("sector")]
    if not sectors:
        return 0, None
    hits = sum(1 for s in sectors if s == sector)
    if not hits:
        return 0, None
    share = hits / len(sectors)
    pretty = sector.replace("_", " ")
    return round(W_SECTOR * share), f"{hits} of {len(sectors)} signals in {pretty}"


def _hiring_volume(signals: list[dict]) -> tuple[int, str]:
    n = len(signals)
    scaled = round(W_VOLUME * min(n, VOLUME_SATURATION) / VOLUME_SATURATION)
    plural = "signals" if n != 1 else "signal"
    return scaled, f"{n} hiring {plural} detected in the reporting window"


def _relationship(tier: str | None) -> tuple[int, str]:
    if tier == "A":
        return W_RELATIONSHIP, "Tier A watchlist client — established relationship"
    if tier == "B":
        return round(W_RELATIONSHIP * 0.75), "Tier B watchlist client"
    if tier == "C":
        return round(W_RELATIONSHIP * 0.5), "Tier C watchlist client"
    # Not a penalty: a new name is a genuine opportunity, just a colder one.
    return round(W_RELATIONSHIP * 0.25), "Not currently on the watchlist — net-new opportunity"


def _region_fit(region: str | None, signals: list[dict]) -> tuple[int, str | None]:
    if not region:
        return 0, None
    regions = [s.get("geography") for s in signals if s.get("geography")]
    if not regions:
        return 0, None
    hits = sum(1 for r in regions if r == region)
    if not hits:
        return 0, f"No {region} activity — candidate would need to relocate"
    return round(W_REGION * hits / len(regions)), f"{hits} of {len(regions)} signals in {region}"


def _recency(signals: list[dict], now: datetime) -> tuple[int, str | None]:
    stamps = [d for d in (_parse_stamp(s.get("captured_at")) for s in signals) if d]
    if not stamps:
        return 0, None

    age_days = (now - max(stamps)).total_seconds() / 86400
    if age_days >= RECENCY_HORIZON_DAYS:
        return 0, f"Most recent signal is {int(age_days)} days old"
    scaled = round(W_RECENCY * (1 - age_days / RECENCY_HORIZON_DAYS))
    if age_days < 1:
        return scaled, "Signals from the last 24 hours"
    return scaled, f"Most recent signal {int(age_days)} day(s) ago"


def _skills_overlap(skills: list[str] | None, signals: list[dict]) -> tuple[int, str | None]:
    """How many of the candidate's skills appear in what this company is hiring for.

    The most specific evidence held about a candidate, and the old model never
    looked at it: skills were parsed from the CV, shown in the UI, and compared
    with nothing.

    Matched as whole words against the full advert text rather than fuzzily. A
    skill is a noun somebody either asked for or did not, and "SAP" should not
    half-match "SAP-adjacent" reasoning the way a job title legitimately can.
    """
    if not skills:
        return 0, None

    blob = " ".join((s.get("raw_content") or "") for s in signals).lower()
    if not blob.strip():
        return 0, None

    matched = [
        skill for skill in skills
        if skill and re.search(rf"(?<!\w){re.escape(skill.lower())}(?!\w)", blob)
    ]
    if not matched:
        return 0, None

    share = len(matched) / len(skills)
    shown = ", ".join(matched[:4]) + ("…" if len(matched) > 4 else "")
    return (round(W_SKILLS * share),
            f"{len(matched)} of {len(skills)} skills appear in their adverts: {shown}")


def _signal_quality(signals: list[dict]) -> tuple[int, str | None]:
    """What kind of signals these are, not merely how many.

    Six routine vacancies and six postings around a new project are not the same
    buying moment. Counting them identically was the largest thing the old model
    could not see.

    Averaged rather than summed, so a company is not rewarded for volume twice —
    `_hiring_volume` already covers that.
    """
    cats = [s.get("signal_category") for s in signals if s.get("signal_category")]
    if not cats:
        return 0, None

    weights = [CATEGORY_WEIGHT.get(c, DEFAULT_CATEGORY_WEIGHT) for c in cats]
    mean = sum(weights) / len(weights)

    strongest = max(cats, key=lambda c: CATEGORY_WEIGHT.get(c, DEFAULT_CATEGORY_WEIGHT))
    best_weight = CATEGORY_WEIGHT.get(strongest, DEFAULT_CATEGORY_WEIGHT)
    label = strongest.replace("_", " ")

    if best_weight >= 0.8:
        n = sum(1 for c in cats if c == strongest)
        note = f"{n} {label} signal{'s' if n != 1 else ''} — a decision point, not routine hiring"
    else:
        note = f"Mostly {label} signals"
    return round(W_SIGNAL_QUALITY * mean), note


def _momentum(signals: list[dict], now: datetime) -> tuple[int, str | None]:
    """Whether hiring is accelerating against this company's own recent history.

    The difference between a good account and a good *week* to call one. Measured
    against the company itself rather than against other companies, because a
    firm that always posts forty roles is not newsworthy at forty.

    Returns nothing when there is no history to compare against — a company seen
    only this week has no trend, and inventing one from a single point is how the
    old velocity table came to report every company as rising.
    """
    recent, prior = 0, 0
    for s in signals:
        stamp = _parse_stamp(s.get("captured_at"))
        if stamp is None:
            continue
        age = (now - stamp).total_seconds() / 86400
        if age <= MOMENTUM_WINDOW_DAYS:
            recent += 1
        elif age <= MOMENTUM_WINDOW_DAYS * (1 + MOMENTUM_BASELINE_WINDOWS):
            prior += 1

    if not prior:
        return 0, None
    baseline = prior / MOMENTUM_BASELINE_WINDOWS
    if baseline <= 0 or recent <= baseline:
        return 0, None

    growth = (recent - baseline) / baseline
    scaled = round(W_MOMENTUM * min(growth / MOMENTUM_SATURATION, 1.0))
    if not scaled:
        return 0, None

    # A percentage off a baseline below one signal a window is arithmetic, not a
    # trend: two signals against 0.3 reads as "up 567%", which is true and
    # useless. The points still stand — going from almost nothing to something
    # is real — but the sentence a consultant repeats on a call should not carry
    # a number that will not survive being questioned.
    if baseline < MOMENTUM_MIN_BASELINE_TO_QUOTE:
        return scaled, (f"{recent} signals this window against almost none before — "
                        f"first sustained hiring we have seen from them")
    return scaled, (f"Hiring up {round(growth * 100)}% on their own recent average "
                    f"({recent} this window against {baseline:.1f})")


def _seniority_fit(years: int | None, signals: list[dict]) -> tuple[int, str | None]:
    """Whether the candidate's experience matches the level being advertised.

    Stops a graduate and a twenty-year planner scoring alike on the same title.
    Silent when either side is unknown: no marks, and no evidence line claiming
    a fit that was never established.
    """
    if years is None:
        return 0, None

    levels: list[int] = []
    for s in signals:
        head = (s.get("raw_content") or "").split("|", 1)[0].lower()
        for marker, implied in SENIORITY_MARKERS:
            if marker in head:
                levels.append(implied)
                break
    if not levels:
        return 0, None

    implied = sum(levels) / len(levels)
    gap = abs(years - implied)
    if gap >= SENIORITY_TOLERANCE_YEARS:
        return 0, (f"Roles look pitched around {implied:.0f} years' experience; "
                   f"the candidate has {years}")
    scaled = round(W_SENIORITY * (1 - gap / SENIORITY_TOLERANCE_YEARS))
    return scaled, f"Roles pitched around {implied:.0f} years — candidate has {years}"


def _confidence(signals: list[dict], now: datetime) -> tuple[str, str]:
    """How much evidence stands behind the score, reported beside it.

    Three signals from one week and forty across a month can produce the same
    number. A consultant deciding whether to make the call should be able to tell
    those apart, and the score alone cannot say so.
    """
    n = len(signals)
    days = {str(s.get("captured_at"))[:10] for s in signals if s.get("captured_at")}
    spread = len(days)

    if n >= 8 and spread >= 2:
        return "high", f"{n} signals across {spread} collections"
    if n >= 3:
        return "medium", f"{n} signals across {spread} collection{'s' if spread != 1 else ''}"
    return "low", f"only {n} signal{'s' if n != 1 else ''} — treat as a lead, not a finding"


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def match_profile(
    profile: dict[str, Any],
    signals: list[dict[str, Any]],
    limit: int = 10,
    now: datetime | None = None,
) -> list[MatchResult]:
    """Rank companies for one candidate.

    `profile` uses the API's camelCase keys; `signals` are classified rows from
    Mode Monitor. Pure: no database, no network, so it is fully testable.
    """
    now = now or datetime.now(timezone.utc)

    title = profile.get("currentTitle") or profile.get("current_title")
    sector = profile.get("sector")
    region = profile.get("region")
    skills = profile.get("skills") or []
    years = profile.get("yearsExperience")
    if years is None:
        years = profile.get("years_experience")

    by_company: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        name = (s.get("company_name") or "").strip()
        # "Unknown" is what the classifier emits when it could not identify the
        # employer; those rows cannot be approached, so they are not candidates.
        if name and name.lower() != "unknown":
            by_company[name].append(s)

    results: list[MatchResult] = []
    for company, rows in by_company.items():
        role_pts, role_ev = _role_demand(title, rows)
        skills_pts, skills_ev = _skills_overlap(skills, rows)
        quality_pts, quality_ev = _signal_quality(rows)
        sector_pts, sector_ev = _sector_fit(sector, rows)
        momentum_pts, momentum_ev = _momentum(rows, now)
        volume_pts, volume_ev = _hiring_volume(rows)
        tier = next((r.get("watchlist_tier") for r in rows if r.get("watchlist_tier")), None)
        rel_pts, rel_ev = _relationship(tier)
        seniority_pts, seniority_ev = _seniority_fit(years, rows)
        region_pts, region_ev = _region_fit(region, rows)
        recency_pts, recency_ev = _recency(rows, now)

        total = (role_pts + skills_pts + quality_pts + sector_pts + momentum_pts
                 + volume_pts + rel_pts + seniority_pts + region_pts + recency_pts)

        # Ordered by how much a consultant would lead with it on a call, which
        # is not the same as by weight: momentum and signal quality answer "why
        # now", and that is the harder half of an opening line.
        evidence = [e for e in (role_ev, momentum_ev, quality_ev, skills_ev, volume_ev,
                                sector_ev, seniority_ev, rel_ev, region_ev, recency_ev) if e]

        confidence, confidence_note = _confidence(rows, now)

        dominant_region = max(
            {r.get("geography") for r in rows if r.get("geography")} or {None},
            key=lambda g: sum(1 for r in rows if r.get("geography") == g),
        )
        dominant_sector = max(
            {r.get("sector") for r in rows if r.get("sector")} or {None},
            key=lambda sec: sum(1 for r in rows if r.get("sector") == sec),
        )

        results.append(MatchResult(
            company=company,
            score=min(100, total),
            region=dominant_region,
            sector=dominant_sector,
            tier=tier,
            is_new_prospect=tier is None,
            signal_count=len(rows),
            evidence=evidence,
            confidence=confidence,
            confidence_note=confidence_note,
            breakdown={
                "role": role_pts, "skills": skills_pts, "signalQuality": quality_pts,
                "sector": sector_pts, "momentum": momentum_pts, "volume": volume_pts,
                "relationship": rel_pts, "seniority": seniority_pts,
                "region": region_pts, "recency": recency_pts,
            },
        ))

    # Company name as the final key keeps the order stable when scores tie.
    results.sort(key=lambda m: (-m.score, m.company))
    log.info("match_profile: scored %d companies, returning %d",
             len(results), min(limit, len(results)))
    return results[:limit]
