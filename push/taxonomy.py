"""A skills vocabulary for industrial recruitment, with aliases and kinds.

The scorer's first version matched skills as literal substrings against a list
of twenty-five keywords. That fails in both directions and neither failure is
visible in the result:

* **It misses.** A CV saying "Primavera P6" and an advert saying "P6" do not
  match. Neither do "SAP PM" and "SAP Plant Maintenance", "Cert IV" and
  "Certificate IV", "RCM" and "reliability centred maintenance". The candidate
  simply scores lower for having written it the other way.
* **It over-matches.** "Lean" appears inside "cleaning". "AMT" appears inside
  a dozen unrelated words. The old code guarded the second case with a word
  boundary but not the first: a bare token can still be the wrong sense of the
  word.

So skills are canonicalised through this table before anything is compared, and
what gets compared is the canonical name. Aliases are matched longest-first, so
"ms project" wins over a bare "project".

**Kind matters as much as the name.** A certification is a gate — a role either
requires the ticket or it does not, and the candidate either holds it or does
not. A software package is a strong signal of the same working environment. A
method is weaker: everybody claims lean. The scorer weights a match by its kind
rather than treating every overlap as equivalent, which is the difference
between "holds the confined space ticket they are asking for" and "both said
the word improvement".

This is deliberately a hand-written table rather than an inferred one. A
taxonomy learned from this corpus would be learned from a few thousand adverts
in two countries, and would encode their quirks as though they were facts about
the trade. Somebody who knows the sector can read this file and disagree with
it, which is the property that matters while nobody has calibrated anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: How much a match of each kind is worth, relative to each other.
#:
#: A ticket is a gate: they asked for it, the candidate has it, and that is
#: nearly decisive for whether the application survives a first pass. A software
#: package means the same tools and usually the same working environment. A
#: method or a domain word is real but weak — it is the part of a CV everybody
#: writes, so matching it separates nobody.
KIND_WEIGHT: dict[str, float] = {
    "certification": 1.0,
    "software": 0.85,
    "equipment": 0.7,
    "method": 0.5,
    "domain": 0.4,
}
DEFAULT_KIND_WEIGHT = 0.5

#: Plain-language names for the kinds, for the UI. The reader should not have to
#: know the internal word.
KIND_LABEL: dict[str, str] = {
    "certification": "Ticket or certification",
    "software": "Software or system",
    "equipment": "Equipment or plant",
    "method": "Method or practice",
    "domain": "Domain knowledge",
}


@dataclass(frozen=True)
class Skill:
    """One canonical skill and the ways it gets written."""

    name: str
    kind: str
    #: Written forms that mean the same thing. The canonical name is always
    #: matched too and does not need repeating here.
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def weight(self) -> float:
        return KIND_WEIGHT.get(self.kind, DEFAULT_KIND_WEIGHT)

    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind.replace("_", " ").title())


#: The vocabulary. Ordered by kind for reading; matching does not depend on it.
#:
#: Scope is the sectors MIOS actually watches — mining, construction, oil and
#: gas, energy transition, defence and logistics, in Australia and Papua New
#: Guinea. A general-purpose skills list would be mostly dead weight here and
#: would dilute the rarity weighting with terms this corpus never contains.
SKILLS: tuple[Skill, ...] = (
    # ---- Tickets and certifications -------------------------------------
    Skill("White Card", "certification",
          ("white card", "construction induction", "cpccwhs1001")),
    Skill("Confined Space", "certification",
          ("confined space", "confined spaces", "rii wha", "enter and work in confined")),
    Skill("Working at Heights", "certification",
          ("working at heights", "work at heights", "working at height", "ewp")),
    Skill("Cert IV", "certification",
          ("cert iv", "cert 4", "certificate iv", "certificate 4")),
    Skill("Cert III", "certification",
          ("cert iii", "cert 3", "certificate iii", "certificate 3")),
    Skill("PMP", "certification", ("pmp", "project management professional")),
    Skill("HV Switching", "certification",
          ("hv switching", "high voltage switching", "hv ticket")),
    Skill("Rigging Ticket", "certification",
          ("rigging", "dogging", "dogman", "rigger")),
    Skill("Forklift Licence", "certification",
          ("forklift", "lf licence", "lf license", "high risk work licence")),
    Skill("HR Licence", "certification",
          ("hr licence", "hr license", "heavy rigid", "mc licence", "hc licence")),
    Skill("First Aid", "certification", ("first aid", "cpr certificate")),
    Skill("G1/G2/G3", "certification", ("g1 g2 g3", "gas testing", "g2 ticket")),

    # ---- Software and systems -------------------------------------------
    Skill("SAP", "software", ("sap", "sap pm", "sap plant maintenance", "sap mm", "sap erp")),
    Skill("Pronto", "software", ("pronto", "pronto xi")),
    Skill("Maximo", "software", ("maximo", "ibm maximo")),
    Skill("Ellipse", "software", ("ellipse", "abb ellipse", "mincom ellipse")),
    Skill("AMT", "software", ("amt", "asset management tool", "komatsu amt")),
    Skill("Primavera P6", "software",
          ("primavera p6", "primavera", "p6", "oracle primavera")),
    Skill("MS Project", "software", ("ms project", "microsoft project", "msp")),
    Skill("AutoCAD", "software", ("autocad", "auto cad")),
    Skill("SolidWorks", "software", ("solidworks", "solid works")),
    Skill("Revit", "software", ("revit", "autodesk revit")),
    Skill("Civil 3D", "software", ("civil 3d", "civil3d")),
    Skill("Navisworks", "software", ("navisworks",)),
    Skill("Deswik", "software", ("deswik",)),
    Skill("Surpac", "software", ("surpac", "geovia surpac")),
    Skill("Vulcan", "software", ("vulcan", "maptek vulcan")),
    Skill("MineSched", "software", ("minesched", "mine sched")),
    Skill("Pitram", "software", ("pitram",)),
    Skill("CMMS", "software", ("cmms", "computerised maintenance management")),
    Skill("SCADA", "software", ("scada",)),
    Skill("PI System", "software", ("pi system", "osisoft", "aveva pi")),
    Skill("Power BI", "software", ("power bi", "powerbi")),

    # ---- Equipment and plant --------------------------------------------
    Skill("Fixed Plant", "equipment", ("fixed plant", "processing plant", "crushing plant")),
    Skill("Mobile Plant", "equipment", ("mobile plant", "mobile fleet", "heavy mobile")),
    Skill("Conveyors", "equipment", ("conveyor", "conveyors", "conveyor systems")),
    Skill("Drill and Blast", "equipment", ("drill and blast", "drill & blast", "d&b")),
    Skill("Longwall", "equipment", ("longwall",)),
    Skill("Rotating Equipment", "equipment",
          ("rotating equipment", "pumps and compressors", "turbomachinery")),
    Skill("Hydraulics", "equipment", ("hydraulic", "hydraulics")),
    Skill("Pipeline", "equipment", ("pipeline", "pipelines", "pipe spooling")),

    # ---- Methods and practices ------------------------------------------
    Skill("Shutdown Planning", "method",
          ("shutdown planning", "shutdown", "shutdowns", "turnaround", "turnarounds", "ta")),
    Skill("RCM", "method",
          ("rcm", "reliability centred maintenance", "reliability centered maintenance")),
    Skill("FMEA", "method", ("fmea", "failure mode and effects")),
    Skill("HAZOP", "method", ("hazop", "hazid", "hazard and operability")),
    Skill("Root Cause Analysis", "method", ("root cause", "rca", "5 whys")),
    Skill("Six Sigma", "method", ("six sigma", "6 sigma", "black belt", "green belt")),
    Skill("Lean", "method", ("lean manufacturing", "lean six sigma", "lean")),
    Skill("ISO 55000", "method", ("iso 55000", "iso55000", "iso 55001")),
    Skill("ISO 9001", "method", ("iso 9001", "iso9001")),
    Skill("Precommissioning", "method",
          ("precommissioning", "pre-commissioning", "commissioning")),
    Skill("Earned Value", "method", ("earned value", "evm")),
    Skill("Cost Control", "method", ("cost control", "cost controller", "cost engineering")),
    Skill("Planning and Scheduling", "method",
          ("planning and scheduling", "scheduler", "planner scheduler")),

    # ---- Domain ----------------------------------------------------------
    Skill("FIFO", "domain", ("fifo", "fly in fly out", "dido")),
    Skill("Open Pit", "domain", ("open pit", "open cut", "surface mining")),
    Skill("Underground", "domain", ("underground", "u/g mining")),
    Skill("LNG", "domain", ("lng", "liquefied natural gas")),
    Skill("Renewables", "domain",
          ("renewables", "wind farm", "solar farm", "bess", "battery storage")),
    Skill("Rail", "domain", ("rail", "railway", "heavy haul")),
    Skill("Ports", "domain", ("port", "ports", "wharf", "stevedoring")),
    Skill("Defence", "domain", ("defence", "defense", "aukus", "sovereign capability")),
    Skill("Tailings", "domain", ("tailings", "tsf")),
    Skill("HSE", "domain", ("hse", "heq", "whs", "ohs", "safety management system")),
)


def _build_index() -> list[tuple[re.Pattern[str], Skill]]:
    """Alias patterns, longest first.

    Longest-first matters: "ms project" must win over "project", and "lean six
    sigma" over "lean". A dict keyed on the alias would give whichever the
    iteration order happened to reach.
    """
    pairs: list[tuple[str, Skill]] = []
    for skill in SKILLS:
        for alias in (skill.name.lower(), *(a.lower() for a in skill.aliases)):
            pairs.append((alias, skill))
    pairs.sort(key=lambda p: (-len(p[0]), p[0]))
    # Word-bounded on both sides. `\b` is wrong here because several aliases end
    # in a non-word character ("drill & blast", "g1 g2 g3"), where `\b` asserts
    # the opposite of what is meant.
    return [(re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)"), skill) for alias, skill in pairs]


_INDEX = _build_index()

#: Canonical names, for callers that want the vocabulary itself.
CANONICAL: dict[str, Skill] = {s.name.lower(): s for s in SKILLS}


def find(text: str) -> list[Skill]:
    """Every skill mentioned in this text, canonicalised and de-duplicated.

    Order is the vocabulary's, not the text's, so two calls on the same content
    always produce the same list — the scorer's output has to be reproducible.
    """
    if not text:
        return []
    lowered = text.lower()
    seen: dict[str, Skill] = {}
    for pattern, skill in _INDEX:
        if skill.name in seen:
            continue
        if pattern.search(lowered):
            seen[skill.name] = skill
    return [s for s in SKILLS if s.name in seen]


def canonicalise(raw_skills: list[str]) -> list[Skill]:
    """Turn free-text skills from a CV or a form into vocabulary entries.

    A term the vocabulary does not carry is dropped rather than kept as itself.
    That is a real loss and worth stating: the scorer can only compare what it
    can canonicalise on both sides, and an unknown term matched literally would
    reintroduce exactly the substring problems this module exists to remove.
    `unknown` below reports what was dropped, so the UI can show it.
    """
    out: dict[str, Skill] = {}
    for raw in raw_skills or []:
        for skill in find(str(raw)):
            out[skill.name] = skill
    return [s for s in SKILLS if s.name in out]


def unknown(raw_skills: list[str]) -> list[str]:
    """Skills the vocabulary could not place. Shown rather than silently lost."""
    return [str(r) for r in (raw_skills or []) if str(r).strip() and not find(str(r))]
