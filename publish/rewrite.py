"""Gemini rewrites the report's prose. It never supplies the facts.

The project spec calls for an LLM "Report Generator" behind Mode Publish, and
this is it. But a client-facing document that a founder signs is the worst place
in the system for an invented figure, so the split is deliberate:

    publish/report.py   counts the rows and writes plain prose  (the facts)
    publish/rewrite.py  asks Gemini to make that prose read well (the wording)

Gemini is given the computed text and told to rewrite it. It is not given the
database, and it is not asked to analyse anything. Every number in its output is
then checked against the numbers in the input, and a section that introduces one
is discarded in favour of the computed version. That check is the point of this
module — without it, "hiring grew strongly across the quarter" is one token away
from "hiring grew 40% across the quarter".

Free-tier economics, shared with classification:

* The whole report is one API call, not one per section. On a 20-call daily
  budget, spending seven on a document nobody has read yet would be absurd.
* The counter in `kv_store` is the same one `classify_pending` uses, so a report
  cannot quietly starve the pipeline that produces next week's signals.
* If the quota is gone, the key is missing, or Gemini fails, the report is still
  produced — with the computed prose and a note saying why.

Two sections are never sent:

* **Looking Ahead** is empty by design; there is nothing to rewrite, and an LLM
  asked to fill it would invent an outlook.
* **Methodology** carries the disclaimers ("none are estimated", "a sample of
  the market, not a census"). Those are load-bearing sentences and are not worth
  the risk of a paraphrase softening them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agents.signal_analyst import (
    DAILY_API_CALL_LIMIT,
    _ensure_kv_store,
    _get_daily_api_calls,
    _increment_daily_api_calls,
    _throttle,
)
from config.settings import settings
from loader.db import connect
from publish.report import Section

log = logging.getLogger(__name__)

#: Headings whose wording is left exactly as computed. See the module docstring.
NEVER_REWRITE = ("Looking Ahead", "Methodology")

#: One report is one call. Room for seven rewritten sections with headroom.
MAX_OUTPUT_TOKENS = 8000

SYSTEM_PROMPT = """You are an editor at an industrial recruitment firm, preparing \
a quarterly market report for clients in Australia and Papua New Guinea.

You will be given sections of a report that were assembled from counted data. \
Rewrite each one so it reads as professional prose a client would expect.

RULES — these are absolute:
1. Do NOT introduce any number, percentage, date or quantity that is not already \
in the section you were given. Not one.
2. Do NOT write numbers as words to get around rule 1.
3. Do NOT add findings, causes, predictions, comparisons to other periods, or \
any claim the given text does not make. You have not seen the underlying data.
4. Keep every company name, role title and figure exactly as written.
5. If a section says something is absent or cannot be measured, keep that caveat. \
Do not soften it into something more confident.
6. Keep a similar length. This is an edit, not an expansion.

You are improving the writing. You are not analysing the market."""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["heading", "body"],
            },
        }
    },
    "required": ["sections"],
}

#: Matches 396, 41%, 1,250, 2026-Q3, 12.5 — anything a reader takes as a fact.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


@dataclass
class RewriteOutcome:
    """What happened, in terms the UI can show without guessing."""

    sections: list[Section]
    used_llm: bool
    #: Present when the computed prose was kept. Written for a human.
    reason: str | None = None
    rejected: list[str] | None = None
    calls_used: int = 0


def _numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUMBER.finditer(text)}


def invented_numbers(computed: str, rewritten: str) -> set[str]:
    """Numbers present in the rewrite that were not in the computed text.

    Deliberately one-directional: dropping a figure is an editorial choice, but
    adding one is fabrication.

    A number spelled as a word ("four sources") slips past this, which is why
    rule 2 of the prompt forbids it — the check is a backstop, not the only
    defence.
    """
    return _numbers(rewritten) - _numbers(computed)


def _remaining_quota(target: str | Path | None) -> int:
    try:
        with connect(target) as conn:
            _ensure_kv_store(conn)
            return DAILY_API_CALL_LIMIT - _get_daily_api_calls(conn)
    except Exception as exc:  # noqa: BLE001 - never block a report on the counter
        log.warning("publish.rewrite: could not read the quota counter (%s)", exc)
        return 0


def rewrite(
    sections: list[Section],
    *,
    target: str | Path | None = None,
    gemini_caller: Callable[..., dict[str, Any]] | None = None,
) -> RewriteOutcome:
    """Rewrite the generated sections. Falls back to computed prose on any doubt."""
    candidates = [
        s for s in sections
        if s.source == "generated" and s.heading not in NEVER_REWRITE and s.body.strip()
    ]
    if not candidates:
        return RewriteOutcome(sections, used_llm=False, reason="Nothing to rewrite.")

    if gemini_caller is None:
        if not settings.gemini_api_key:
            return RewriteOutcome(
                sections, used_llm=False,
                reason="No Gemini key is configured, so the report uses its computed wording.",
            )
        remaining = _remaining_quota(target)
        if remaining <= 0:
            return RewriteOutcome(
                sections, used_llm=False,
                reason=(f"The daily Gemini limit of {DAILY_API_CALL_LIMIT} calls is used up. "
                        "The report uses its computed wording; regenerate tomorrow to "
                        "have it rewritten."),
            )
        try:
            from agents.signal_analyst import _build_gemini_caller

            gemini_caller = _build_gemini_caller()
        except Exception as exc:  # noqa: BLE001
            return RewriteOutcome(sections, used_llm=False,
                                  reason=f"Gemini is unavailable ({exc}).")
        _throttle()

    prompt = "\n\n".join(
        f"### {s.heading}\n{s.body}" for s in candidates
    )
    prompt = (
        f"Rewrite each of the following {len(candidates)} sections. Return every "
        "one, with its heading unchanged.\n\n" + prompt
    )

    try:
        raw = gemini_caller(SYSTEM_PROMPT, prompt, RESPONSE_SCHEMA)
    except Exception as exc:  # noqa: BLE001 - a failed rewrite is not a failed report
        log.warning("publish.rewrite: Gemini call failed (%s)", exc)
        return RewriteOutcome(sections, used_llm=False,
                              reason=f"The rewrite could not be completed ({exc}).")

    # The call happened, so it counts against the budget whatever comes back.
    calls_used = 1
    try:
        with connect(target) as conn:
            _ensure_kv_store(conn)
            _increment_daily_api_calls(conn)
    except Exception as exc:  # noqa: BLE001
        log.warning("publish.rewrite: could not record the API call (%s)", exc)

    returned = {
        str(item.get("heading", "")).strip(): str(item.get("body", "")).strip()
        for item in (raw.get("sections") or [])
    }

    out: list[Section] = []
    rejected: list[str] = []
    accepted = 0
    for s in sections:
        new_body = returned.get(s.heading)
        if s not in candidates or not new_body:
            out.append(s)
            continue
        invented = invented_numbers(s.body, new_body)
        if invented:
            log.warning("publish.rewrite: %r introduced %s — keeping computed prose",
                        s.heading, sorted(invented))
            rejected.append(s.heading)
            out.append(s)
            continue
        out.append(Section(s.heading, new_body, s.source))
        accepted += 1

    log.info("publish.rewrite: %d/%d sections rewritten, %d rejected",
             accepted, len(candidates), len(rejected))

    reason = None
    if rejected:
        reason = (
            f"{len(rejected)} section(s) were left as computed because the rewrite "
            f"introduced figures that were not in the data: {', '.join(rejected)}."
        )
    return RewriteOutcome(out, used_llm=accepted > 0, reason=reason,
                          rejected=rejected or None, calls_used=calls_used)
