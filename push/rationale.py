"""The written half of a Mode Push result.

The score stays deterministic. This adds what a score cannot carry: a sentence a
consultant can actually send, and a flag where the evidence reads differently
from how it counts.

**It never changes the ranking.** That was a deliberate decision, and it is the
same one the scorer's own docstring has argued since it was written: the score
decides who gets contacted, so it has to be reproducible and defensible. "94%
match" with no traceable reason is not something a consultant can act on, and a
model that quietly reorders the list makes every number above it unfalsifiable.
So the model reads the ranking and writes about it; it does not vote.

**It may disagree, out loud.** A model that can only agree is decoration. Where
the text and the numbers point different ways — a high score whose evidence is
all one stale project, a low score sitting on an obvious fit the keyword
matching missed — that becomes a visible flag beside the row rather than a
silent adjustment to it. A consultant can then look, which is the point.

**One call for the whole list.** Never one per company: the free Gemini tier
allows twenty requests a day in total and the weekly pipeline already spends
some of them. A per-company call would exhaust the allowance on the first
candidate.

**It is optional.** Every failure mode here — no key, spent quota, malformed
reply — returns the ranking unannotated rather than failing the request. The
deterministic result is the product; this is an improvement on it.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from llm import PURPOSE_PUSH, LLMError, caller_for

log = logging.getLogger(__name__)

#: How far down the list is worth writing about. A consultant works the top of a
#: ranking; paying for prose about the twentieth company spends quota on
#: something nobody reads.
ANNOTATE_TOP_N = 8

#: Verdicts the model may return. Deliberately coarse: a model asked for a
#: number produces false precision, and this exists beside a score that already
#: carries the precision.
FIT_STRONG = "strong"
FIT_POSSIBLE = "possible"
FIT_WEAK = "weak"
FITS = (FIT_STRONG, FIT_POSSIBLE, FIT_WEAK)

SYSTEM_PROMPT = """\
You advise a recruitment team at Easy Skill, an industrial staffing company \
working in Australian and Papua New Guinean mining, energy and construction.

You are given a candidate and a ranked list of companies. The ranking is already \
decided by a scoring model and you must not attempt to change it. Your job is to \
write the part a score cannot carry.

For each company return:

- `rationale`: one sentence a consultant could say on a call, naming the \
specific reason this candidate suits this company now. Concrete, drawn only \
from the evidence given. No greeting, no sign-off, no adjectives that are not \
earned by the evidence.
- `fit`: "strong", "possible" or "weak" — your own read, independent of the \
score.
- `caveat`: a short note ONLY where the evidence reads differently from how it \
scored, or where something a consultant should check before calling. Otherwise \
null. Do not invent a caveat to seem thorough.

Rules you must not break:

- Use only the evidence supplied. Do not introduce companies, people, projects, \
numbers or dates that are not in it.
- Do not restate the score or invent a percentage of your own.
- If the evidence is too thin to say anything specific, say so plainly in the \
rationale rather than writing something generic.
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "company": {"type": "STRING"},
            "rationale": {"type": "STRING"},
            "fit": {"type": "STRING", "enum": list(FITS)},
            "caveat": {"type": "STRING", "nullable": True},
        },
        "required": ["company", "rationale", "fit"],
    },
}


def build_prompt(profile: dict[str, Any], matches: list[dict[str, Any]]) -> str:
    """What the model is shown: the candidate, and the evidence behind each rank.

    The breakdown goes in as well as the evidence lines. A model that can see
    a score was carried by relationship rather than by role demand can say
    something useful about it — and that is precisely the disagreement worth
    surfacing.
    """
    lines = [
        "CANDIDATE",
        f"  Current title : {profile.get('currentTitle') or 'unknown'}",
        f"  Sector        : {profile.get('sector') or 'unknown'}",
        f"  Region        : {profile.get('region') or 'unknown'}",
        f"  Experience    : {profile.get('yearsExperience') or 'unknown'} years",
        f"  Skills        : {', '.join(profile.get('skills') or []) or 'none recorded'}",
        "",
        "RANKED COMPANIES",
    ]
    for m in matches:
        lines.append(f"\n{m.get('rank')}. {m.get('co')} — score {m.get('score')}/100 "
                     f"({m.get('confidence', 'unknown')} confidence, "
                     f"{m.get('signalCount', 0)} signals, {m.get('rel', 'unknown')})")
        for e in m.get("evidence") or []:
            lines.append(f"     - {e}")
        breakdown = m.get("breakdown") or {}
        if breakdown:
            carried = ", ".join(f"{k} {v}" for k, v in
                                sorted(breakdown.items(), key=lambda kv: -kv[1]) if v)
            lines.append(f"     points: {carried or 'none'}")
    return "\n".join(lines)


def _clean(entry: Any, known: set[str]) -> dict[str, Any] | None:
    """One model reply, or None if it cannot be trusted.

    Rejects an entry naming a company that was not in the list. A model that
    invents a company here would put a name in front of a consultant that
    nothing in the pipeline supports.
    """
    if not isinstance(entry, dict):
        return None
    company = str(entry.get("company") or "").strip()
    if company not in known:
        log.warning("push.rationale: discarding an entry for %r, which was not ranked",
                    company)
        return None
    rationale = str(entry.get("rationale") or "").strip()
    if not rationale:
        return None
    fit = str(entry.get("fit") or "").strip().lower()
    if fit not in FITS:
        # An unlabelled verdict is dropped rather than defaulted: guessing
        # "possible" would put a judgement in the model's mouth.
        fit = ""
    caveat = entry.get("caveat")
    caveat = str(caveat).strip() if caveat else None
    return {"company": company, "rationale": rationale, "fit": fit, "caveat": caveat or None}


def annotate(
    profile: dict[str, Any],
    matches: list[dict[str, Any]],
    *,
    top_n: int = ANNOTATE_TOP_N,
    caller: Callable[..., Any] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Add a rationale to the top of a ranking. Returns (matches, note).

    `note` explains why nothing was added, when nothing was. The ranking comes
    back either way, in the same order it went in — this function cannot
    reorder, and the tests hold it to that.
    """
    if not matches:
        return matches, None

    subject = matches[:top_n]
    known = {str(m.get("co")) for m in subject}

    if caller is None:
        try:
            caller = caller_for(PURPOSE_PUSH)
        except LLMError as exc:
            log.info("push.rationale: no model available (%s)", exc)
            return matches, str(exc)

    try:
        raw = caller(SYSTEM_PROMPT, build_prompt(profile, subject), RESPONSE_SCHEMA)
    except LLMError as exc:
        log.warning("push.rationale: model call failed (%s)", exc)
        return matches, str(exc)
    except Exception as exc:  # noqa: BLE001 - never fail the ranking for this
        log.warning("push.rationale: model call failed (%s)", exc)
        return matches, f"{type(exc).__name__}: {exc}"

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return matches, "The model did not return JSON."
    if not isinstance(raw, list):
        return matches, "The model did not return a list of companies."

    by_company = {}
    for entry in raw:
        cleaned = _clean(entry, known)
        if cleaned:
            by_company[cleaned["company"]] = cleaned

    if not by_company:
        return matches, "The model returned nothing usable."

    for m in matches:
        extra = by_company.get(str(m.get("co")))
        if not extra:
            continue
        m["rationale"] = extra["rationale"]
        m["fit"] = extra["fit"]
        m["caveat"] = extra["caveat"]
        #: True where the model's read and the score point different ways. Shown
        #: as a flag rather than acted on: it is a reason for a human to look,
        #: not a correction to the ranking.
        m["disagrees"] = bool(
            (extra["fit"] == FIT_WEAK and int(m.get("score") or 0) >= 70)
            or (extra["fit"] == FIT_STRONG and int(m.get("score") or 0) < 40)
        )

    annotated = sum(1 for m in matches if m.get("rationale"))
    log.info("push.rationale: annotated %d of %d companies in one call",
             annotated, len(matches))
    return matches, None
