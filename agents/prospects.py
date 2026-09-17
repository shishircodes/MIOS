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
  real employer, so the agency must never be offered as one.
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


def is_agency(company: str | None) -> bool:
    """Whether a company is a recruitment agency or labour-hire business."""
    name = (company or "").strip()
    if not name:
        return False
    return name.casefold() in competitor_names() or bool(AGENCY_PATTERN.search(name))


def is_prospect(company: str | None, sector: str | None, source_type: str | None,
                *, watchlisted: bool) -> bool:
    """Whether this signal names a company Easy Skill should consider approaching."""
    if watchlisted:
        return False
    name = (company or "").strip()
    if not name or name.casefold() == "unknown":
        return False
    if (sector or "") not in RELEVANT_SECTORS:
        return False
    if (source_type or "") == "tender":
        return False
    return not is_agency(name)
