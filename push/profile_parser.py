"""CV text -> structured candidate profile, using rules rather than an LLM.

A general-purpose CV parser is hard. This one is not general-purpose: Easy Skill
recruits into mining, oil & gas, construction, defence and energy transition
across Australia and Papua New Guinea, so a vocabulary of roles, sectors and
places covers the ground an LLM would otherwise be needed for — deterministically,
for free, and without sending anyone's CV to a third party.

The output is a *draft*. Every field carries a confidence, and the API hands the
draft back to the BD team to confirm or correct before anything is saved. That
keeps a human in the loop, which is both better UX and the honest way to ship a
parser that will sometimes be wrong.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Domain vocabulary
# --------------------------------------------------------------------------

#: Sector keywords, checked against the whole CV. Ordered by specificity so a
#: "LNG plant" CV lands in oil_gas rather than the broader mining bucket.
SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "oil_gas": ("oil and gas", "oil & gas", "lng", "upstream", "downstream", "refinery",
                "petroleum", "drilling", "wellhead", "offshore platform", "fpso",
                "pipeline integrity", "gas plant"),
    "mining": ("mining", "mine site", "open pit", "underground mine", "ore", "concentrator",
               "processing plant", "fifo", "shutdown", "mineral", "gold mine", "copper",
               "iron ore", "bauxite", "tailings", "haul truck", "drill and blast"),
    "energy_transition": ("renewable", "solar farm", "wind farm", "battery storage",
                          "hydrogen", "decarbonis", "decarboniz", "energy transition"),
    "defence": ("defence", "defense", "naval", "shipbuilding", "sovereign capability",
                "aegis", "submarine", "munitions"),
    "construction": ("construction", "civil works", "infrastructure project", "tier 1 builder",
                     "earthworks", "concrete", "structural steel", "commissioning",
                     "site supervisor", "project engineer"),
}

#: Role families. The first match wins, so longer titles are listed first.
ROLE_KEYWORDS: tuple[str, ...] = (
    "maintenance planner", "reliability engineer", "maintenance superintendent",
    "maintenance supervisor", "mechanical supervisor", "electrical supervisor",
    "shutdown coordinator", "shutdown planner", "turnaround manager",
    "process engineer", "process operator", "plant operator", "mine planner",
    "mining engineer", "geologist", "metallurgist", "drill and blast engineer",
    "project engineer", "project manager", "construction manager", "site manager",
    "site superintendent", "superintendent", "hse advisor", "hseq advisor",
    "safety advisor", "safety manager", "quality manager", "contracts administrator",
    "cost controller", "planner scheduler", "scheduler", "planner",
    "electrical engineer", "mechanical engineer", "civil engineer", "structural engineer",
    "instrumentation technician", "electrician", "boilermaker", "fitter",
    "heavy diesel fitter", "diesel mechanic", "rigger", "scaffolder", "operator",
    "general manager", "operations manager", "engineering manager", "supervisor",
    "technician", "engineer", "manager",
)

#: Place names that pin a CV to a region. PNG first — an Australian CV rarely
#: names a PNG site, but a PNG CV very often names Australian ones too.
PNG_PLACES = ("papua new guinea", "png", "port moresby", "lae", "lihir", "porgera",
              "ok tedi", "tabubil", "hidden valley", "ramu", "kutubu", "hides",
              "morobe", "madang", "bougainville")
AU_PLACES = ("australia", "western australia", "queensland", "new south wales",
             "victoria", "south australia", "northern territory", "tasmania",
             "perth", "brisbane", "sydney", "melbourne", "adelaide", "darwin",
             "pilbara", "newman", "karratha", "port hedland", "kalgoorlie",
             "mackay", "gladstone", "bowen basin", "hunter valley", "wa", "qld",
             "nsw", "nt", "sa", "vic")

#: Certifications and tickets worth surfacing — they often decide a placement.
SKILL_KEYWORDS = (
    "sap", "pronto", "maximo", "ellipse", "amt", "primavera", "p6", "ms project",
    "autocad", "solidworks", "revit", "iso 55000", "rcm", "fmea", "pmp",
    "cert iv", "white card", "confined space", "working at heights", "hazop",
    "six sigma", "lean", "shutdown planning", "turnaround", "cmms",
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
#: AU and PNG numbers, with or without country code and common separators.
_PHONE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?)?\d{3,4}[\s-]?\d{3,4}")
_YEARS = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)(?:[’']?\s*(?:of\s*)?(?:experience|exp))?",
    re.I,
)
_YEAR_RANGE = re.compile(r"\b(19|20)\d{2}\s*[-–—]\s*((19|20)\d{2}|present|current)\b", re.I)
_URLISH = re.compile(r"(https?://|www\.|linkedin\.com)", re.I)

#: Lines that are section headings, not names.
_HEADING_WORDS = {
    "curriculum vitae", "resume", "résumé", "cv", "profile", "personal details",
    "contact", "contact details", "summary", "professional summary", "objective",
    "career objective", "experience", "work experience", "employment history",
    "education", "qualifications", "skills", "key skills", "referees", "references",
}


@dataclass
class ParsedProfile:
    """A draft profile plus how confident each field is.

    `confidence` maps field name -> "high" | "medium" | "low". The UI uses it to
    highlight what a human should check, so a wrong guess is cheap.
    """

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    current_title: str | None = None
    sector: str | None = None
    years_experience: int | None = None
    region: str | None = None
    skills: list[str] = field(default_factory=list)
    confidence: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fullName": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "currentTitle": self.current_title,
            "sector": self.sector,
            "yearsExperience": self.years_experience,
            "region": self.region,
            "skills": self.skills,
            "confidence": self.confidence,
        }


# --------------------------------------------------------------------------
# Field extractors
# --------------------------------------------------------------------------


def _looks_like_name(line: str) -> bool:
    """A person's name: 2-4 capitalised words, no digits, not a heading."""
    s = line.strip().strip(",.;:")
    if not (4 <= len(s) <= 60) or any(ch.isdigit() for ch in s):
        return False
    if s.lower() in _HEADING_WORDS or _EMAIL.search(s) or _URLISH.search(s):
        return False
    words = s.split()
    if not (2 <= len(words) <= 4):
        return False
    # ALL CAPS names are common on CVs, so accept those too.
    return all(w[:1].isupper() or w.isupper() for w in words if w)


def _extract_name(lines: list[str]) -> tuple[str | None, str]:
    # Names sit at the top; scanning further finds referees instead.
    for line in lines[:8]:
        if _looks_like_name(line):
            s = line.strip().strip(",.;:")
            return (s.title() if s.isupper() else s), "high"
    for line in lines[:8]:
        s = line.strip().strip(",.;:")
        if s and s.lower() not in _HEADING_WORDS and not any(c.isdigit() for c in s):
            return s[:60], "low"
    return None, "low"


def _extract_phone(text: str) -> tuple[str | None, str]:
    for m in _PHONE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        # Below 8 digits it is a date or a postcode; above 15 it is not a number.
        if 8 <= len(digits) <= 15:
            return m.group(0).strip(), "medium"
    return None, "low"


def _extract_title(text: str, lines: list[str]) -> tuple[str | None, str]:
    lowered = text.lower()
    # Prefer a role named near the top — that is the current one.
    head = "\n".join(lines[:12]).lower()
    for role in ROLE_KEYWORDS:
        if role in head:
            return role.title(), "high"
    for role in ROLE_KEYWORDS:
        if role in lowered:
            return role.title(), "medium"
    return None, "low"


def _extract_sector(text: str) -> tuple[str | None, str]:
    lowered = text.lower()
    hits = {s: sum(lowered.count(k) for k in kws) for s, kws in SECTOR_KEYWORDS.items()}
    hits = {s: n for s, n in hits.items() if n}
    if not hits:
        return None, "low"
    best = max(hits, key=lambda s: hits[s])
    # A clear winner is trustworthy; a near-tie is a judgement call for a human.
    ranked = sorted(hits.values(), reverse=True)
    strong = len(ranked) == 1 or ranked[0] >= ranked[1] * 2
    return best, "high" if strong else "medium"


def _extract_years(text: str) -> tuple[int | None, str]:
    stated = [int(m.group(1)) for m in _YEARS.finditer(text) if 0 < int(m.group(1)) <= 60]
    if stated:
        # "15 years experience" beats a mention of "3 years at X" further down.
        return max(stated), "high"

    # Otherwise infer from the span of employment dates.
    years: list[int] = []
    for m in _YEAR_RANGE.finditer(text):
        start = int(m.group(0)[:4])
        end_raw = m.group(2).lower()
        end = 2026 if end_raw in ("present", "current") else int(end_raw)
        if 1960 < start <= end <= 2100:
            years.extend([start, end])
    if years:
        span = max(years) - min(years)
        if 0 < span <= 60:
            return span, "medium"
    return None, "low"


def _extract_region(text: str) -> tuple[str | None, str]:
    lowered = text.lower()
    png = sum(1 for p in PNG_PLACES if re.search(rf"\b{re.escape(p)}\b", lowered))
    au = sum(1 for p in AU_PLACES if re.search(rf"\b{re.escape(p)}\b", lowered))
    if not png and not au:
        return None, "low"
    if png and png >= au:
        return "PNG", "high" if png > au else "medium"
    return "AU", "high" if au > png else "medium"


def _extract_skills(text: str) -> list[str]:
    lowered = text.lower()
    found = [s for s in SKILL_KEYWORDS if re.search(rf"\b{re.escape(s)}\b", lowered)]
    # Longest-first so "shutdown planning" is preferred over a bare "shutdown".
    return sorted(set(found), key=lambda s: (-len(s), s))[:12]


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def parse_profile(text: str) -> ParsedProfile:
    """Parse CV text into a draft profile. Never raises — worst case is empty."""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    if not lines:
        return ParsedProfile(confidence={"full_name": "low"})

    name, name_conf = _extract_name(lines)
    email_m = _EMAIL.search(text)
    phone, phone_conf = _extract_phone(text)
    title, title_conf = _extract_title(text, lines)
    sector, sector_conf = _extract_sector(text)
    years, years_conf = _extract_years(text)
    region, region_conf = _extract_region(text)
    skills = _extract_skills(text)

    profile = ParsedProfile(
        full_name=name,
        email=email_m.group(0) if email_m else None,
        phone=phone,
        current_title=title,
        sector=sector,
        years_experience=years,
        region=region,
        skills=skills,
        confidence={
            "full_name": name_conf,
            "email": "high" if email_m else "low",
            "phone": phone_conf,
            "current_title": title_conf,
            "sector": sector_conf,
            "years_experience": years_conf,
            "region": region_conf,
            "skills": "high" if len(skills) >= 3 else "medium" if skills else "low",
        },
    )
    log.info(
        "parse_profile: title=%r sector=%r years=%s region=%s skills=%d",
        profile.current_title, profile.sector, profile.years_experience,
        profile.region, len(profile.skills),
    )
    return profile
