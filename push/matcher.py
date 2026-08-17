"""Score companies against a candidate profile, using Mode Monitor's signals.

This is the heart of Mode Push: the BD team has a consultant, and needs to know
which companies to approach *now*, with a reason they can put in an email.

Deterministic on purpose. Every point in a score traces to a named contributor,
and each contributor produces an evidence line — the bullets the BD team reads
before deciding to make contact. An LLM score would be neither reproducible nor
explainable, and "94% match" with no reason is not something a consultant can
act on.

Scoring, out of 100:

    35  role demand   — is this company hiring for the candidate's discipline?
    20  sector fit    — does the candidate's sector match the company's?
    15  hiring volume — how much are they hiring right now?
    12  relationship  — existing watchlist client vs a new name
    10  region fit    — same market as the candidate
     8  recency       — how fresh the signals are

Weights are constants below rather than being buried in the code, so they can be
tuned once the BD team has used it and can say what actually predicts a placement.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from rapidfuzz import fuzz

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Weights. These sum to 100.
# --------------------------------------------------------------------------

W_ROLE = 35
W_SECTOR = 20
W_VOLUME = 15
W_RELATIONSHIP = 12
W_REGION = 10
W_RECENCY = 8

#: A title this similar counts as the same discipline. rapidfuzz token_set_ratio
#: already handles word order, so this is about tolerating "Snr"/"Senior" and
#: "Maint." — not about matching unrelated roles.
ROLE_SIMILARITY_FLOOR = 62

#: Hiring volume that earns full marks. Beyond this the extra tells you little.
VOLUME_SATURATION = 6

#: Signals older than this contribute nothing to recency.
RECENCY_HORIZON_DAYS = 30


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
        }


# --------------------------------------------------------------------------
# Individual contributors
# --------------------------------------------------------------------------


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
    stamps: list[datetime] = []
    for s in signals:
        raw = s.get("captured_at")
        if not raw:
            continue
        try:
            d = datetime.fromisoformat(str(raw))
            stamps.append(d if d.tzinfo else d.replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    if not stamps:
        return 0, None

    age_days = (now - max(stamps)).total_seconds() / 86400
    if age_days >= RECENCY_HORIZON_DAYS:
        return 0, f"Most recent signal is {int(age_days)} days old"
    scaled = round(W_RECENCY * (1 - age_days / RECENCY_HORIZON_DAYS))
    if age_days < 1:
        return scaled, "Signals from the last 24 hours"
    return scaled, f"Most recent signal {int(age_days)} day(s) ago"


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
        sector_pts, sector_ev = _sector_fit(sector, rows)
        volume_pts, volume_ev = _hiring_volume(rows)
        tier = next((r.get("watchlist_tier") for r in rows if r.get("watchlist_tier")), None)
        rel_pts, rel_ev = _relationship(tier)
        region_pts, region_ev = _region_fit(region, rows)
        recency_pts, recency_ev = _recency(rows, now)

        total = role_pts + sector_pts + volume_pts + rel_pts + region_pts + recency_pts

        # Ordered so the strongest reason leads the bullet list.
        evidence = [e for e in (role_ev, volume_ev, sector_ev, rel_ev, region_ev, recency_ev) if e]

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
            breakdown={
                "role": role_pts, "sector": sector_pts, "volume": volume_pts,
                "relationship": rel_pts, "region": region_pts, "recency": recency_pts,
            },
        ))

    # Company name as the final key keeps the order stable when scores tie.
    results.sort(key=lambda m: (-m.score, m.company))
    log.info("match_profile: scored %d companies, returning %d",
             len(results), min(limit, len(results)))
    return results[:limit]
