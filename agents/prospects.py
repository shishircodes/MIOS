"""Who counts as a new prospect. Decided in code, not by the model.

The classifier's prompt says a new prospect is a named company in one of Easy
Skill's sectors that is not already on the watchlist. The model agreed in the
prompt and ignored it in practice: of 920 classified signals in production, 210
flagged as new prospects were in no relevant sector at all (an airline, a
central bank, universities, aid agencies), 50 were recruitment agencies, and all
20 AusTender notices named the government buyer as a prospect. The digest's
"new names" list and Mode Push both read this flag, so each of those was a
suggestion to pitch to somebody who will never hire through Easy Skill.

The rules are simple and fixed, so they live here where they can be tested and
applied the same way to new signals (`agents.signal_analyst`) and to rows
already stored (`loader.rematch`):

* a company already on the watchlist is a client, not a prospect;
* an unnamed employer is nobody to approach;
* a company outside the five sectors is not a prospect for this business;
* a tender names the buyer — the prospect is whoever wins the work, which the
  notice cannot say;
* a recruitment or labour-hire agency is a competitor. Its advert hides the
  real employer, so the agency must never be offered as one;
* a government body owns or funds work but does not hire the crews: whoever
  wins the contract does, and that contractor is the prospect.

A second look at production on 5 Oct 2026 found both of the last two still
getting through. Fourteen flagged rows were agencies whose names carry none of
the words below (Mining People International ten times, WorkPac, Programmed,
Zenith Search), and eighteen were governments, departments and councils named
in news as the owner of a project. The named agencies are now in
`config/competitors.json`, matched anywhere in a name so "WorkPac - Mining Qld"
counts; government bodies are recognised by the words in their names.

Government-owned companies that employ their own workforce (Inland Rail,
Marinus Link, Snowy Hydro) carry none of those words and stay prospects, which
is deliberate.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

RELEVANT_SECTORS = frozenset({"mining", "oil_gas", "construction", "defence", "energy_transition"})

COMPETITORS_PATH = Path(__file__).resolve().parents[1] / "config" / "competitors.json"

#: Words that mark a recruitment or labour-hire business by its name. Whole
#: words only, so "Recruit" inside an unrelated name does not trip it. Named
#: agencies that carry none of these words go in `config/competitors.json`.
AGENCY_PATTERN = re.compile(
    r"\b(recruit(?:ment|ing|ers?|s)?|staffing|personnel|labou?r[\s-]hire|workforce|talent|headhunt(?:ers?|ing)?|"
    r"employment\s+services|resourcing)\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def competitor_names(path: Path = COMPETITORS_PATH) -> frozenset[str]:
    """Named competitors, casefolded. Empty when the list is missing."""
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    return frozenset(str(n).strip().casefold() for n in entries if str(n).strip())


#: Words that mark a government, a department or a public authority by its
#: name, and the lenders and bodies that fund public work. Whole words only.
GOVERNMENT_PATTERN = re.compile(
    r"\b(governments?|govt|departments?|dept|ministry|council|shire|city\s+of|commission|"
    r"authority|parliament|treasury|defence\s+force|royal\s+australian\s+(?:navy|air\s+force)|"
    r"australian\s+army|police|embassy|world\s+bank|united\s+nations|european\s+union|"
    r"asian\s+development\s+bank)\b",
    re.IGNORECASE,
)

#: What a job board prints when the advertiser is not named. Not a company.
_NOBODY = frozenset({"unknown", "private advertiser", "confidential", "company confidential"})


@lru_cache(maxsize=1)
def _competitor_pattern() -> re.Pattern[str] | None:
    names = sorted(competitor_names(), key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.IGNORECASE)


def is_agency(company: str | None) -> bool:
    """Whether a company is a recruitment agency or labour-hire business."""
    name = (company or "").strip()
    if not name:
        return False
    # A listed agency anywhere in the name: boards print "WorkPac - Mining Qld".
    named = _competitor_pattern()
    return bool(named and named.search(name)) or bool(AGENCY_PATTERN.search(name))


def is_government(company: str | None) -> bool:
    """Whether a name is a government, a department, a council or the like."""
    return bool(GOVERNMENT_PATTERN.search((company or "").strip()))


def is_prospect(company: str | None, sector: str | None, source_type: str | None,
                *, watchlisted: bool) -> bool:
    """Whether this signal names a company Easy Skill should consider approaching."""
    if watchlisted:
        return False
    name = (company or "").strip()
    if not name or name.casefold() in _NOBODY:
        return False
    if (sector or "") not in RELEVANT_SECTORS:
        return False
    if (source_type or "") == "tender":
        return False
    return not is_agency(name) and not is_government(name)
