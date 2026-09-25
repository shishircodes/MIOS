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

from agents.prospects import RELEVANT_SECTORS, is_agency
from push import taxonomy
from push.rarity import AVERAGE_HEADROOM, RARE_AT, Rarity
from push.rarity import build as build_rarity

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

#: Words that place a role's seniority, as the band of experience the level
#: usually asks for: (word, minimum years, maximum years or None for no ceiling).
#: Deliberately small and explicit: an inferred ladder would be guesswork
#: dressed as a measurement.
#:
#: A band, not a point. The first version mapped each word to one number —
#: "principal" meant exactly 12 years — and scored the distance from it, so a
#: 16-year candidate lost more than half the points for a principal role they
#: are plainly qualified for. Senior levels have a floor and no ceiling: nobody
#: is too experienced to be a principal or a director.
SENIORITY_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("graduate", 0, 2), ("trainee", 0, 2), ("apprentice", 0, 2), ("entry level", 0, 2),
    ("junior", 0, 3), ("intermediate", 3, 7), ("mid level", 3, 7),
    ("senior", 6, None), ("lead", 8, None), ("manager", 8, None),
    ("principal", 10, None), ("superintendent", 10, None), ("head of", 12, None),
    ("director", 15, None), ("general manager", 15, None), ("chief", 18, None),
)

#: How far below a band's floor the fit fades to nothing. Narrow, because
#: missing experience is the gap a client notices first.
SENIORITY_SHORTFALL_YEARS = 5
#: How far above a band's ceiling it fades to nothing. Wide, because an
#: experienced candidate in a junior role is a risk, not a mismatch of skills.
SENIORITY_OVERQUALIFIED_YEARS = 10

#: Below this much of the model being applicable, a score is reported at low
#: confidence whatever it says. Normalising to 100 means a company judged on a
#: third of the contributors can still reach 90; that is arithmetically correct
#: and a poor thing to act on.
NARROW_ASSESSMENT = 60

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

#: The most a company can score when none of its adverts are for the
#: candidate's discipline and none ask for their skills. Relationship, sector,
#: region and recency can otherwise carry such a company to the mid-fifties —
#: a good account, but not a place this candidate can be put forward.
NO_DEMAND_CAP = 40

#: How close a word in an advert title must be to the candidate's trade word
#: ("planner", "electrician") to count as the same trade. High enough to accept
#: plurals and small spelling variants, low enough to refuse "electrical".
HEAD_WORD_SIMILARITY = 90

#: Legal-form words that make one employer look like two ("Downer" and "Downer
#: Group", "BHP" and "BHP Group"). Trailing only, so "Group Five" stays intact.
LEGAL_SUFFIXES = frozenset({
    "pty", "ltd", "limited", "inc", "incorporated", "plc", "corp", "corporation",
    "co", "company", "group", "holdings", "llc",
})


def is_advert(signal: dict) -> bool:
    """Whether a signal is a job advert rather than news or a tender.

    Rows without a source type are treated as adverts: every signal predating
    the column was one, and so is every row a test builds by hand.
    """
    return (signal.get("source_type") or "job_board") == "job_board"


def company_key(name: str | None) -> str:
    """The name one employer goes by, however it was written in an advert."""
    words = _WORD.findall((name or "").casefold())
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def exclusion_reason(signal: dict) -> str | None:
    """Why a signal names no company a candidate could be put forward to.

    * An unnamed employer cannot be approached.
    * A tender names the government buyer, not an employer that is hiring.
    * A recruitment agency is a competitor, not a client.
    * A company outside Easy Skill's sectors is not a client either — the same
      rule the digest, dashboard and quarterly report apply.
    """
    name = (signal.get("company_name") or "").strip()
    if not name or name.casefold() == "unknown":
        return "unnamed"
    if signal.get("source_type") == "tender":
        return "tender"
    if is_agency(name):
        return "agency"
    sector = signal.get("sector")
    if sector and sector not in RELEVANT_SECTORS:
        return "sector"
    return None


def exclusions(signals: list[dict]) -> dict[str, int]:
    """How many signals each exclusion rule removed, for the payload."""
    counts: dict[str, int] = defaultdict(int)
    for s in signals:
        reason = exclusion_reason(s)
        if reason:
            counts[reason] += 1
    return dict(counts)


def market_as_of(signals: list[dict]) -> datetime | None:
    """The newest capture in the market: the moment the data describes.

    Momentum and recency are measured from here rather than from the clock. A
    weekly scrape read on a Thursday is the same week it was on the Monday; with
    the clock as the reference, every company's momentum fell to nothing once a
    week passed without a run, and every recency score drifted by the day.
    """
    stamps = [d for d in (_parse_stamp(s.get("captured_at")) for s in signals) if d]
    return max(stamps) if stamps else None


def market_rarity(signals: list[dict]) -> Rarity:
    """Skill rarity measured over the market's job adverts only.

    News articles rarely name a skill, so counting them made every skill look
    rarer than it is in the adverts a candidate is actually compared with.
    """
    return build_rarity([s for s in signals if is_advert(s) and not exclusion_reason(s)])


#: What each contributor is called, and what it actually asks. Kept here rather
#: than in the interface so the drawer explaining a score reads from the same
#: place the score is computed — the two cannot drift into telling different
#: stories about the same number.
CONTRIBUTOR_LABEL: dict[str, tuple[str, str]] = {
    "role": ("Role demand",
             "Are they hiring for this candidate's discipline, with seniority "
             "words set aside so a title matches on the trade rather than the level?"),
    "skills": ("Skills overlap",
               "Do the candidate's skills appear in their adverts — weighted by "
               "how rare each one is in this market, and by whether it is a "
               "ticket, a system or a turn of phrase?"),
    "signalQuality": ("Signal quality",
                      "What kind of signals these are. A new project or a leadership "
                      "change is a decision point; routine vacancies are not."),
    "sector": ("Sector fit", "Is their hiring in the candidate's sector?"),
    "momentum": ("Momentum",
                 "Is their hiring accelerating against their own recent baseline?"),
    "volume": ("Hiring volume", "How much they are hiring right now."),
    "relationship": ("Relationship",
                     "An existing watchlist client, or a new name."),
    "seniority": ("Seniority fit",
                  "Does the candidate's experience match the level the adverts are "
                  "pitched at?"),
    "region": ("Region fit", "Same market as the candidate."),
    "recency": ("Recency", "How fresh the signals behind this are."),
}


@dataclass
class Contribution:
    """One contributor's part of a score, with everything needed to defend it.

    The score alone cannot be argued with, and a consultant putting a company in
    front of a client needs to be able to say why it is there. So each part
    carries what it was worth, what it earned, and the sentence explaining it —
    or, when it could not be judged, that fact and nothing invented in its place.
    """

    key: str
    label: str
    #: What this contributor asks. Plain language, for the drawer.
    asks: str
    #: Points available. The share of the model this contributor represents.
    weight: int
    #: Points earned, or None when there was nothing to judge.
    earned: int | None
    evidence: str | None = None
    #: Why it could not be judged. Present only when `earned` is None, and
    #: written for a reader: "no skills recorded on the profile" is actionable,
    #: "not assessed" is not.
    unassessed_because: str | None = None

    @property
    def assessed(self) -> bool:
        return self.earned is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "asks": self.asks,
            "weight": self.weight,
            "earned": self.earned,
            "evidence": self.evidence,
            "unassessedBecause": self.unassessed_because,
            #: How much of what was available this contributor earned, for a bar
            #: in the drawer. None when unassessed, so the bar is absent rather
            #: than drawn at zero — a contributor that could not be judged did
            #: not score badly, and a zero-length bar says it did.
            "share": (round(self.earned / self.weight, 3)
                      if self.earned is not None and self.weight else None),
        }


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
    #: Points earned, and the weight of the contributors that could be judged.
    #: `score` is these two scaled to 100 — kept so a reader can see the working
    #: rather than a number that appears from nowhere.
    earned: int = 0
    assessable: int = 100
    not_assessed: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    breakdown: dict[str, int] = field(default_factory=dict)
    #: The full working, one entry per contributor including the ones that could
    #: not be judged. What the detail drawer renders.
    contributions: list[Contribution] = field(default_factory=list)
    #: Which of the candidate's skills were found, and how rare each is here.
    skill_detail: list[dict[str, Any]] = field(default_factory=list)
    #: False when nothing they advertise matches the candidate's discipline or
    #: skills. Such companies rank below every one that does, and are held at
    #: NO_DEMAND_CAP.
    demand: bool = True
    #: The score before that cap, for ordering companies held at it.
    uncapped: int = 0

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
            "earned": self.earned,
            "assessable": self.assessable,
            "notAssessed": self.not_assessed,
            "contributions": [c.to_dict() for c in self.contributions],
            "skillDetail": self.skill_detail,
            "demand": self.demand,
            #: Present only when the cap applied, so the drawer can say what the
            #: score would have been.
            "uncapped": self.uncapped if self.uncapped != self.score else None,
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


#: Words that state a level rather than a trade. Stripped before two titles are
#: compared, because seniority is scored separately and a title match that
#: includes it charges the same fact twice: "Senior Planner" against "Planner"
#: loses points on the title AND again on seniority, while "Senior Planner"
#: against "Senior Boilermaker" gains points for agreeing about nothing that
#: matters.
_LEVEL_WORDS = frozenset({
    "graduate", "trainee", "apprentice", "junior", "jnr", "intermediate",
    "senior", "snr", "sr", "lead", "leading", "principal", "chief", "head",
    "assistant", "deputy", "acting", "trainee", "entry", "level", "grade",
    "i", "ii", "iii", "iv", "1", "2", "3", "4",
})

_WORD = re.compile(r"[a-z0-9]+")


def _discipline(title: str | None) -> str:
    """A title reduced to the trade it names.

    "Senior Maintenance Planner" and "Maintenance Planner" both become
    "maintenance planner", so they compare as the same discipline — which they
    are. The level is not discarded from the model, only from this comparison;
    `_seniority_fit` reads it from the advert text separately.
    """
    if not title:
        return ""
    words = [_singular(w) for w in _WORD.findall(str(title).lower()) if w not in _LEVEL_WORDS]
    return " ".join(words)


def _singular(word: str) -> str:
    """"planners" -> "planner", so an advert for several counts as one trade.

    Deliberately crude — applied to both sides of every comparison, it only has
    to be consistent, not correct English. "Process" and "gas" are left alone.
    """
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _role_head(signal: dict) -> str:
    """The job title from a signal. Scrapers put it in the first segment."""
    content = (signal.get("raw_content") or "")
    return content.split("|", 1)[0].strip() or content[:80]


def _names_trade(trade: str, advert_discipline: str) -> bool:
    """Whether an advert title names the candidate's trade word.

    `token_set_ratio` alone scores any title sharing one word with the
    candidate's highly — "Maintenance Planner" against "Maintenance
    Coordinator" came out at 73, a match. The shared word there is the domain;
    the trade is the last word, and a coordinator is not a planner.
    """
    return any(fuzz.ratio(trade, word) >= HEAD_WORD_SIMILARITY
               for word in advert_discipline.split())


def _title_similarity(wanted: str, advert_title: str) -> int:
    """How closely an advert title matches the candidate's discipline, 0–100."""
    advert = _discipline(advert_title)
    trade = wanted.split()[-1]
    if not _names_trade(trade, advert):
        return 0
    return round(fuzz.token_set_ratio(wanted, advert))


def _role_demand(title: str | None, signals: list[dict]) -> tuple[int | None, str | None]:
    """How strongly this company is hiring for the candidate's discipline.

    `signals` are the company's job adverts. `None` when the profile carries no
    job title: that is a gap in what we know about the candidate, not a company
    that fails to match.
    """
    if not title:
        return None, None
    wanted = _discipline(title)
    if not wanted:
        # A title made entirely of seniority words ("Senior Manager") leaves no
        # discipline to compare. Seniority is its own contributor; pretending
        # this one was assessed would score the same fact twice.
        return None, None

    heads = [_role_head(s) for s in signals]
    scored = [(_title_similarity(wanted, h), h) for h in heads if h]
    if not scored:
        # Assessed, and the answer is no: the company is in the news, but has
        # not advertised anything in the window.
        return 0, "No job adverts from them in this window — news or project activity only"

    best, best_text = max(scored, key=lambda p: p[0])
    if best < ROLE_SIMILARITY_FLOOR:
        n = len(scored)
        return 0, f"No “{title.strip()}” role among their {n} advert{'s' if n != 1 else ''}"

    # Rescale: the floor earns nothing, a perfect title match earns full marks.
    scaled = round(W_ROLE * (best - ROLE_SIMILARITY_FLOOR) / (100 - ROLE_SIMILARITY_FLOOR))
    matching = sum(1 for score, _ in scored if score >= ROLE_SIMILARITY_FLOOR)
    plural = "roles" if matching != 1 else "role"
    return scaled, f"{matching} {plural} matching “{title}” — closest: {best_text[:70]}"


def _sector_fit(sector: str | None, signals: list[dict]) -> tuple[int | None, str | None]:
    """`None` when either side is unknown; 0 when both are known and disagree."""
    if not sector:
        return None, None
    sectors = [s.get("sector") for s in signals if s.get("sector")]
    if not sectors:
        return None, None
    hits = sum(1 for s in sectors if s == sector)
    if not hits:
        pretty = sector.replace("_", " ")
        return 0, f"No {pretty} hiring — their activity is in other sectors"
    share = hits / len(sectors)
    pretty = sector.replace("_", " ")
    return round(W_SECTOR * share), f"{hits} of {len(sectors)} signals in {pretty}"


def _hiring_volume(adverts: list[dict]) -> tuple[int, str]:
    """How many job adverts they have out. News and tenders are not hiring."""
    n = len(adverts)
    if not n:
        return 0, "No job adverts in the window"
    scaled = round(W_VOLUME * min(n, VOLUME_SATURATION) / VOLUME_SATURATION)
    return scaled, f"{n} job advert{'s' if n != 1 else ''} in the window"


def _relationship(tier: str | None) -> tuple[int, str]:
    if tier == "A":
        return W_RELATIONSHIP, "Tier A watchlist client — established relationship"
    if tier == "B":
        return round(W_RELATIONSHIP * 0.75), "Tier B watchlist client"
    if tier == "C":
        return round(W_RELATIONSHIP * 0.5), "Tier C watchlist client"
    # Not a penalty: a new name is a genuine opportunity, just a colder one.
    return round(W_RELATIONSHIP * 0.25), "Not currently on the watchlist — net-new opportunity"


def _region_fit(region: str | None, signals: list[dict]) -> tuple[int | None, str | None]:
    if not region:
        return None, None
    # The effective region, not the board's: a PNG role advertised on an
    # Australian board is PNG work, the same rule the digest applies.
    regions = [_region_of(s) for s in signals if _region_of(s)]
    if not regions:
        return None, None
    hits = sum(1 for r in regions if r == region)
    if not hits:
        return 0, f"No {region} activity — candidate would need to relocate"
    return round(W_REGION * hits / len(regions)), f"{hits} of {len(regions)} signals in {region}"


def _region_of(signal: dict) -> str | None:
    return signal.get("region") or signal.get("geography")


def _recency(signals: list[dict], now: datetime) -> tuple[int | None, str | None]:
    stamps = [d for d in (_parse_stamp(s.get("captured_at")) for s in signals) if d]
    if not stamps:
        return None, None

    age_days = (now - max(stamps)).total_seconds() / 86400
    if age_days >= RECENCY_HORIZON_DAYS:
        return 0, f"Most recent signal is {int(age_days)} days old"
    scaled = round(W_RECENCY * (1 - age_days / RECENCY_HORIZON_DAYS))
    if age_days < 1:
        return scaled, "Signals from the last 24 hours"
    return scaled, f"Most recent signal {int(age_days)} day(s) ago"


def _skills_overlap(skills: list[str] | None, signals: list[dict],
                    rarity: Rarity) -> tuple[int | None, str | None, list[dict[str, Any]]]:
    """How many of the candidate's skills appear in what this company is hiring for.

    The most specific evidence held about a candidate, and the old model never
    looked at it: skills were parsed from the CV, shown in the UI, and compared
    with nothing.

    Matched as whole words against the full advert text rather than fuzzily. A
    skill is a noun somebody either asked for or did not, and "SAP" should not
    half-match "SAP-adjacent" reasoning the way a job title legitimately can.
    """
    if not skills:
        # No skills recorded. Scoring zero here would charge the company for a
        # gap in the candidate's profile.
        return None, None, []

    wanted = taxonomy.canonicalise(skills)
    if not wanted:
        # Skills were recorded but none are in the vocabulary, so there is
        # nothing this scorer can compare. Not zero: the candidate has not
        # failed a test, we simply cannot mark it.
        return None, None, []

    blob = " ".join((s.get("raw_content") or "") for s in signals)
    if not blob.strip():
        return None, None, []

    present = {s.name for s in taxonomy.find(blob)}

    # Two separate questions, deliberately not multiplied into one weighting.
    #
    # **Coverage** is how much of what the candidate claims this company asks
    # for, weighted by kind: a ticket is a gate, a method is a turn of phrase.
    #
    # **Strength** is how meaningful those matches are, from how rare they are
    # in this market.
    #
    # Rarity belongs in the second only. Putting it in the denominator too —
    # which is what the first version of this did — cancels it out entirely: a
    # candidate claiming one rare skill and matching it scores exactly what one
    # claiming a common skill and matching it scores, because both matched
    # everything they claimed. That is the flat model this was meant to replace.
    detail: list[dict[str, Any]] = []
    kind_total = 0.0
    kind_matched = 0.0
    for skill in wanted:
        kind_total += skill.weight
        hit = skill.name in present
        if hit:
            kind_matched += skill.weight
        detail.append({
            "name": skill.name,
            "kind": skill.kind,
            "kindLabel": skill.kind_label,
            "matched": hit,
            "rarity": rarity.describe(skill),
        })

    if not kind_total:
        return None, None, detail

    matched = [d for d in detail if d["matched"]]
    if not matched:
        # Assessed: we knew what to look for and none of it is there.
        return 0, "None of the candidate's recorded skills appear in their adverts", detail

    coverage = kind_matched / kind_total
    if rarity.applies:
        mean_rarity = sum(rarity.of(d["name"]) for d in matched) / len(matched)
        strength = min(1.0, AVERAGE_HEADROOM * mean_rarity)
    else:
        # No corpus to measure against, so nothing is known to be unusual. Full
        # marks stay reachable — applying the headroom here would quietly cap
        # every skills score on a small corpus for a reason nobody could see.
        strength = 1.0

    # Sorted by what carries most, so the evidence line leads with the rare
    # ticket rather than whichever skill was typed first.
    lead = sorted(matched, key=lambda d: -rarity.of(d["name"]))
    shown = ", ".join(d["name"] for d in lead[:4]) + ("…" if len(lead) > 4 else "")
    note = ""
    standout = [d["name"] for d in lead if rarity.of(d["name"]) >= RARE_AT][:2]
    if standout:
        note = f" — {', '.join(standout)} {'is' if len(standout) == 1 else 'are'} rare here"
    return (round(W_SKILLS * coverage * strength),
            f"{len(matched)} of {len(wanted)} skills appear in their adverts: "
            f"{shown}{note}", detail)


def _signal_quality(signals: list[dict]) -> tuple[int | None, str | None]:
    """What kind of signals these are, not merely how many.

    Six routine vacancies and six postings around a new project are not the same
    buying moment. Counting them identically was the largest thing the old model
    could not see.

    Averaged rather than summed, so a company is not rewarded for volume twice —
    `_hiring_volume` already covers that.
    """
    cats = [s.get("signal_category") for s in signals if s.get("signal_category")]
    if not cats:
        return None, None

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


def _momentum(signals: list[dict], now: datetime) -> tuple[int | None, str | None]:
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
        # No earlier window to compare against. Not a company that failed to
        # accelerate — a company we have not watched long enough to say.
        return None, None
    baseline = prior / MOMENTUM_BASELINE_WINDOWS
    if baseline <= 0:
        return None, None
    if recent <= baseline:
        return 0, "Hiring steady against their own recent average"

    growth = (recent - baseline) / baseline
    scaled = round(W_MOMENTUM * min(growth / MOMENTUM_SATURATION, 1.0))
    if not scaled:
        return 0, "Hiring steady against their own recent average"

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


def _seniority_fit(years: int | None,
                   signals: list[dict]) -> tuple[int | None, str | None]:
    """Whether the candidate's experience matches the level being advertised.

    Stops a graduate and a twenty-year planner scoring alike on the same title.
    Silent when either side is unknown: no marks, and no evidence line claiming
    a fit that was never established.
    """
    if years is None:
        return None, None

    fits: list[float] = []
    bands: list[tuple[int, int | None]] = []
    for s in signals:
        band = _advertised_band((s.get("raw_content") or "").split("|", 1)[0])
        if band is None:
            continue
        low, high = band
        bands.append(band)
        # Scored advert by advert. Averaging the implied years of a graduate
        # role and a director role would invent a mid-career one nobody posted.
        if years < low:
            fits.append(max(0.0, 1 - (low - years) / SENIORITY_SHORTFALL_YEARS))
        elif high is not None and years > high:
            fits.append(max(0.0, 1 - (years - high) / SENIORITY_OVERQUALIFIED_YEARS))
        else:
            fits.append(1.0)
    if not fits:
        # The adverts do not state a level, so there is nothing to compare with.
        return None, None

    pts = round(W_SENIORITY * sum(fits) / len(fits))

    # The evidence names the level most of the adverts are pitched at.
    low, high = max(set(bands), key=bands.count)
    level = f"{low}+ years" if high is None else f"{low}–{high} years"
    if years < low:
        verdict = f"the candidate's {years} is {low - years} short"
    elif high is not None and years > high:
        verdict = f"the candidate's {years} is {years - high} above it"
    else:
        verdict = f"the candidate's {years} fits"
    return pts, f"Roles pitched at {level} — {verdict}"


def _advertised_band(title: str) -> tuple[int, int | None] | None:
    """The experience band a job title implies, or None when it states no level.

    Whole words only, so "Leading Hand" is not a lead role and "Asset Management
    Planner" is not a manager. Where a title carries several levels ("Senior
    Project Manager") the most senior one decides, because that is the job.
    """
    title = title.lower()
    found = [(low, high) for word, low, high in SENIORITY_BANDS
             if re.search(rf"\b{re.escape(word)}\b", title)]
    if not found:
        return None
    return max(found, key=lambda band: band[0])


def _confidence(signals: list[dict], now: datetime,
                assessable: int = 100) -> tuple[str, str]:
    """How much evidence stands behind the score, reported beside it.

    Three signals from one week and forty across a month can produce the same
    number. A consultant deciding whether to make the call should be able to tell
    those apart, and the score alone cannot say so.

    `assessable` is the other half of that. Normalising a score to 100 means a
    company judged on a third of the model can still reach 90 — arithmetically
    right, and a poor thing to act on. So a narrow assessment caps confidence
    however many signals there are.
    """
    n = len(signals)
    days = {str(s.get("captured_at"))[:10] for s in signals if s.get("captured_at")}
    spread = len(days)
    narrow = assessable < NARROW_ASSESSMENT

    if narrow:
        return "low", (f"scored on {assessable} of 100 points — too little of the model "
                       f"applied to rely on the number")
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
    rarity_model: Rarity | None = None,
    only_company: str | None = None,
) -> list[MatchResult]:
    """Rank companies for one candidate.

    `profile` uses the API's camelCase keys; `signals` are classified rows from
    Mode Monitor. Pure: no database, no network, so it is fully testable.

    `only_company` scores just that employer — for "who fits BHP?" — against
    the same market, so its score is the one the full ranking would give.
    """
    now = now or market_as_of(signals) or datetime.now(timezone.utc)

    title = profile.get("currentTitle") or profile.get("current_title")
    sector = profile.get("sector")
    region = profile.get("region")
    skills = profile.get("skills") or []
    years = profile.get("yearsExperience")
    if years is None:
        years = profile.get("years_experience")

    # One employer however its name was written, and only employers a
    # candidate could be put forward to — see `exclusion_reason`.
    by_company: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        if exclusion_reason(s) is None:
            by_company[company_key(s["company_name"])].append(s)
    if only_company is not None:
        wanted_key = company_key(only_company)
        by_company = {k: v for k, v in by_company.items() if k == wanted_key}

    # Rarity is built from the whole market, not from one company's adverts —
    # "how unusual is this skill" is a question about the market, and computing
    # it per company would make a term rare at a firm that mentioned it once.
    rarity = rarity_model if rarity_model is not None else market_rarity(signals)

    results: list[MatchResult] = []
    for rows in by_company.values():
        # Shown under the name it is most often written as.
        names = [r["company_name"].strip() for r in rows]
        company = min(set(names), key=lambda n: (-names.count(n), len(n), n))

        # What the company is hiring for is read from its job adverts. News and
        # project announcements still count — for what kind of moment this is,
        # the sector, the region and how fresh it all is — but a headline such
        # as "Newmont appoints chief executive" is not a vacancy, and read as
        # one it made a role match, a seniority level and a hiring count.
        adverts = [r for r in rows if is_advert(r)]

        role_pts, role_ev = _role_demand(title, adverts)
        skills_pts, skills_ev, skill_detail = _skills_overlap(skills, adverts, rarity)
        quality_pts, quality_ev = _signal_quality(rows)
        sector_pts, sector_ev = _sector_fit(sector, rows)
        momentum_pts, momentum_ev = _momentum(adverts, now)
        volume_pts, volume_ev = _hiring_volume(adverts)
        tier = next((r.get("watchlist_tier") for r in rows if r.get("watchlist_tier")), None)
        rel_pts, rel_ev = _relationship(tier)
        seniority_pts, seniority_ev = _seniority_fit(years, adverts)
        region_pts, region_ev = _region_fit(region, rows)
        recency_pts, recency_ev = _recency(rows, now)

        # Earned out of assessable, then scaled to 100.
        #
        # A contributor returns None when it had nothing to judge — no skills on
        # the profile, no seniority stated in the adverts, no earlier window to
        # measure a trend against. Counting those as zero would charge the
        # company for gaps in *our* data, which is the same mistake the velocity
        # baseline was fixed for: a week nobody scraped is not a week nobody
        # hired. So an unassessable contributor leaves the denominator instead.
        parts = {
            "role": (role_pts, W_ROLE),
            "skills": (skills_pts, W_SKILLS),
            "signalQuality": (quality_pts, W_SIGNAL_QUALITY),
            "sector": (sector_pts, W_SECTOR),
            "momentum": (momentum_pts, W_MOMENTUM),
            "volume": (volume_pts, W_VOLUME),
            "relationship": (rel_pts, W_RELATIONSHIP),
            "seniority": (seniority_pts, W_SENIORITY),
            "region": (region_pts, W_REGION),
            "recency": (recency_pts, W_RECENCY),
        }
        earned = sum(pts for pts, _ in parts.values() if pts is not None)
        assessable = sum(weight for pts, weight in parts.values() if pts is not None)
        total = round(earned / assessable * 100) if assessable else 0

        # Not hiring for this person. The other contributors describe a good
        # account, and without a cap they carried such companies to the
        # mid-fifties and the top of the list — seven of the top eight for a
        # maintenance planner had no planner role at all.
        no_demand = role_pts == 0 and not skills_pts
        uncapped = total
        capped = no_demand and total > NO_DEMAND_CAP
        if capped:
            total = NO_DEMAND_CAP

        # Ordered by how much a consultant would lead with it on a call, which
        # is not the same as by weight: momentum and signal quality answer "why
        # now", and that is the harder half of an opening line.
        evidence = [e for e in (role_ev, momentum_ev, quality_ev, skills_ev, volume_ev,
                                sector_ev, seniority_ev, rel_ev, region_ev, recency_ev) if e]
        if capped:
            evidence.insert(0, f"Held at {NO_DEMAND_CAP}: nothing they advertise matches "
                               f"this candidate's discipline or skills")

        confidence, confidence_note = _confidence(rows, now, assessable)

        dominant_region = max(
            {_region_of(r) for r in rows if _region_of(r)} or {None},
            key=lambda g: sum(1 for r in rows if _region_of(r) == g),
        )
        dominant_sector = max(
            {r.get("sector") for r in rows if r.get("sector")} or {None},
            key=lambda sec: sum(1 for r in rows if r.get("sector") == sec),
        )

        # Why each unassessed contributor could not be judged. "Not assessed"
        # tells a reader nothing they can act on; "no skills recorded on the
        # profile" tells them to go and add some.
        because = {
            "role": "no job title on the profile",
            "skills": "no recognised skills on the profile",
            "signalQuality": "signals carry no category",
            "sector": "no sector on the profile, or none stated in their adverts",
            "momentum": "no earlier window to measure a trend against",
            "volume": "no job adverts to count",
            "relationship": "no watchlist tier recorded",
            "seniority": "the adverts do not state a level",
            "region": "no region on the profile, or none stated in their adverts",
            "recency": "signals carry no usable date",
        }
        evidence_for = {
            "role": role_ev, "skills": skills_ev, "signalQuality": quality_ev,
            "sector": sector_ev, "momentum": momentum_ev, "volume": volume_ev,
            "relationship": rel_ev, "seniority": seniority_ev,
            "region": region_ev, "recency": recency_ev,
        }
        contributions = [
            Contribution(
                key=key,
                label=CONTRIBUTOR_LABEL[key][0],
                asks=CONTRIBUTOR_LABEL[key][1],
                weight=weight,
                earned=pts,
                evidence=evidence_for.get(key),
                unassessed_because=None if pts is not None else because.get(key),
            )
            for key, (pts, weight) in parts.items()
        ]

        results.append(MatchResult(
            company=company,
            score=min(100, total),
            demand=not no_demand,
            uncapped=min(100, uncapped),
            contributions=contributions,
            skill_detail=skill_detail,
            region=dominant_region,
            sector=dominant_sector,
            tier=tier,
            is_new_prospect=tier is None,
            signal_count=len(rows),
            evidence=evidence,
            confidence=confidence,
            confidence_note=confidence_note,
            earned=earned,
            assessable=assessable,
            breakdown={k: pts for k, (pts, _) in parts.items() if pts is not None},
            #: Contributors that had no input to judge, so a reader can see what
            #: the score does *not* account for rather than assuming it weighed
            #: everything.
            not_assessed=sorted(k for k, (pts, _) in parts.items() if pts is None),
        ))

    # Companies hiring for this candidate first, whatever the others score;
    # then by score, with the uncapped figure ordering those held at the cap,
    # and the name keeping ties stable.
    results.sort(key=lambda m: (not m.demand, -m.score, -m.uncapped, m.company))
    log.info("match_profile: scored %d companies, returning %d",
             len(results), min(limit, len(results)))
    return results[:limit]
