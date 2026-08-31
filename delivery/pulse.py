"""Market Pulse — the digest's written read on the week (spec §9.1).

Everything else in the weekly digest is arithmetic: counts, splits, deltas.
This is the one section the specification asks a model to *write* — "3–5 bullet
summary of broader trends across all geographies and sectors" — and it is the
one section that was still being assembled from f-strings.

Three rules shape what is here.

**Generated once, read many.** A Gemini call belongs in the weekly pipeline run,
not on the dashboard's request path — `/api/digest` runs every time somebody
opens the page. The result goes in `digest_pulse` and every reader loads it from
there.

**No fallback to computed prose.** If the model cannot be reached, the section
is *omitted* and the reason recorded. Template arithmetic presented in the place
a written summary would go reads like a product; an absent section reads like an
absence, which is the truth.

**Interpretation is allowed, but marked.** The model may say "likely shutdown
prep" — a claim the numbers do not prove — because a read on the week that can
only restate the week's arithmetic is not worth a model. Each bullet is tagged
`fact` or `interpretation` so a reader can tell which is which, and the numbers
themselves are still guarded: interpretation means *reasoning beyond* the data,
never *inventing* data.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from config.settings import settings
from loader.db import connect

log = logging.getLogger(__name__)

#: §9.1 asks for 3–5 bullets.
MIN_BULLETS = 3
MAX_BULLETS = 5

#: Short section, short answer. Nothing here needs a long generation.
MAX_OUTPUT_TOKENS = 1200

#: How many ranked signals the model sees. Enough to spot a pattern across
#: companies, few enough that the prompt stays cheap.
EVIDENCE_SIGNALS = 25

#: How many watchlist companies' week-over-week movement to include.
EVIDENCE_VELOCITY = 10

KIND_FACT = "fact"
KIND_INTERPRETATION = "interpretation"

STATUS_GENERATED = "generated"
STATUS_FAILED = "failed"

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["bullets"],
    "properties": {
        "bullets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "kind"],
                "properties": {
                    "text": {"type": "string"},
                    # The model declares which it is. A bullet that reasons past
                    # the evidence is allowed to exist, but not to pass as a
                    # measurement.
                    "kind": {"type": "string", "enum": [KIND_FACT, KIND_INTERPRETATION]},
                },
            },
        },
    },
}

SYSTEM_PROMPT = f"""You write the Market Pulse section of a weekly recruitment
market intelligence digest for Easy Skill, an industrial recruitment firm
operating in Australia and Papua New Guinea. Readers are business development
consultants deciding who to call this week.

Write {MIN_BULLETS}-{MAX_BULLETS} bullets covering broader trends across
geographies and sectors. Not a list of individual jobs — the digest already
lists those separately. Say what changed and what it suggests.

Two kinds of bullet, and you must label each one:

- "fact": restates or combines the figures given to you. Every number in a fact
  bullet must appear in the evidence.
- "interpretation": reasons past the evidence to what it may mean — for example
  that a cluster of maintenance roles at one site suggests shutdown preparation.
  Hedge these: "suggests", "may indicate", "consistent with". Do not present an
  interpretation as an established fact.

Rules:
- Never invent a number. Every figure you use must appear in the evidence below.
  If you want to describe a change you have no number for, describe it without
  one.
- Prefer naming companies and sectors over abstractions.
- One sentence per bullet. No preamble, no headings, no markdown.
- If the evidence is too thin to support a trend, say so plainly in fewer
  bullets rather than padding."""


@dataclass
class PulseOutcome:
    """What a generation attempt produced."""

    bullets: list[dict[str, str]]
    status: str
    note: str | None = None
    signals_analysed: int = 0

    @property
    def ok(self) -> bool:
        return self.status == STATUS_GENERATED and bool(self.bullets)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


def build_evidence(payload: dict[str, Any]) -> str:
    """The measured facts the model is allowed to draw on, as plain text.

    Assembled from the digest payload rather than from the database directly, so
    the model sees exactly the figures the digest publishes. If the two ever
    disagreed, the bullets would be arguing with the table beside them.
    """
    c = payload.get("collection", {})
    lines: list[str] = [
        f"Window: {payload.get('weekLabel') or 'this week'}.",
        f"Signals collected: {c.get('collected', 0)} "
        f"({c.get('jobs', 0)} job postings, {c.get('news', 0)} news articles) "
        f"from {c.get('sources', 0)} sources.",
        f"By market: Australia {c.get('regions', {}).get('AU', 0)}, "
        f"Papua New Guinea {c.get('regions', {}).get('PNG', 0)}.",
        f"Companies not on the watchlist: {c.get('newNames', 0)}.",
    ]

    velocity = payload.get("velocity") or []
    if velocity:
        lines.append("")
        lines.append("Watchlist hiring, this window vs the prior baseline:")
        for row in velocity[:EVIDENCE_VELOCITY]:
            change = row.get("change")
            # `basis` is how many prior windows the baseline averages. A company
            # with no history has no comparison, and saying so stops the model
            # inventing one.
            if change is None or not row.get("basis"):
                movement = "no prior baseline"
            else:
                movement = (f"{'up' if change > 0 else 'down' if change < 0 else 'flat'} "
                            f"{abs(int(change))}% against an average of {row.get('avg')}")
            lines.append(
                f"- {row.get('co')} ({row.get('sector')}, tier {row.get('tier') or '-'}): "
                f"{row.get('wk')} signals this window, {movement}."
            )

    signals = payload.get("signals") or []
    if signals:
        lines.append("")
        lines.append("Top signals this window:")
        for s in signals[:EVIDENCE_SIGNALS]:
            note = (s.get("action") or "").strip()
            lines.append(
                f"- [{s.get('region')}] {s.get('company')} ({s.get('sector')}, "
                f"{s.get('category')}): {s.get('title')}"
                + (f" — {note}" if note else "")
            )

    new_names = payload.get("newNames") or []
    if new_names:
        lines.append("")
        lines.append("New names detected:")
        for n in new_names[:8]:
            lines.append(f"- {n.get('co')} ({n.get('sector')}, {n.get('region')})")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def _clean(bullets: Any, evidence: str) -> tuple[list[dict[str, str]], list[str]]:
    """Keep the bullets that are usable. Returns (kept, reasons_dropped).

    Interpretation is permitted; invented arithmetic is not. The distinction is
    the whole basis on which the section is allowed to reason at all.
    """
    from publish.rewrite import invented_numbers

    kept: list[dict[str, str]] = []
    dropped: list[str] = []
    for item in bullets if isinstance(bullets, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        if not text:
            continue
        if kind not in (KIND_FACT, KIND_INTERPRETATION):
            # An unlabelled bullet cannot be shown as either, and defaulting it
            # to "fact" would launder a guess into a measurement.
            dropped.append(f"unlabelled: {text[:60]}")
            continue
        made_up = invented_numbers(evidence, text)
        if made_up:
            dropped.append(f"figures not in the evidence ({', '.join(sorted(made_up))}): {text[:60]}")
            continue
        kept.append({"text": text, "kind": kind})
    return kept[:MAX_BULLETS], dropped


def generate_pulse(
    payload: dict[str, Any],
    *,
    target: str | Path | None = None,
    gemini_caller: Callable[..., dict[str, Any]] | None = None,
) -> PulseOutcome:
    """Ask Gemini for the week's Market Pulse.

    Never raises: every failure path returns a `failed` outcome carrying the
    reason, which the caller stores so the missing section can be explained.
    """
    signals_analysed = int((payload.get("collection") or {}).get("collected", 0))
    evidence = build_evidence(payload)

    if gemini_caller is None:
        if not settings.gemini_api_key:
            return PulseOutcome([], STATUS_FAILED, "No Gemini key configured.", signals_analysed)

        from publish.rewrite import _remaining_quota

        if _remaining_quota(target) <= 0:
            return PulseOutcome(
                [], STATUS_FAILED,
                "Daily Gemini quota exhausted before the digest ran.", signals_analysed,
            )
        from agents.signal_analyst import _build_gemini_caller

        gemini_caller = _build_gemini_caller()

    if signals_analysed == 0:
        return PulseOutcome([], STATUS_FAILED, "No signals in the window.", signals_analysed)

    try:
        raw = gemini_caller(SYSTEM_PROMPT, evidence, RESPONSE_SCHEMA)
    except Exception as exc:  # noqa: BLE001 - a model outage is not a pipeline failure
        log.warning("pulse: generation failed (%s)", exc)
        return PulseOutcome([], STATUS_FAILED, f"Gemini call failed: {exc}", signals_analysed)

    kept, dropped = _clean((raw or {}).get("bullets"), evidence)
    if not kept:
        reason = "; ".join(dropped) if dropped else "the model returned nothing usable"
        return PulseOutcome([], STATUS_FAILED, f"No usable bullets: {reason}", signals_analysed)

    note = None
    if dropped:
        note = f"{len(dropped)} bullet(s) discarded: {'; '.join(dropped)}"
    return PulseOutcome(kept, STATUS_GENERATED, note, signals_analysed)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def save_pulse(outcome: PulseOutcome, *, window_from: str, window_to: str,
               target: str | Path | None = None) -> None:
    """Record the attempt, whether or not it produced anything.

    A failed week is written too. A missing row and a failed generation would
    otherwise look identical, and "why is there no Market Pulse?" is exactly the
    question this table exists to answer.
    """
    body = json.dumps(outcome.bullets) if outcome.bullets else None
    try:
        with connect(target) as conn:
            conn.execute(
                "INSERT INTO digest_pulse "
                "(window_from, window_to, bullets, status, note, signals_analysed, generated_at) "
                "VALUES (?,?,?,?,?,?,?) "
                # Keyed on the scrape, not on the pair — see the unique index in
                # loader/ingest.py. `window_from` drifts on its own.
                "ON CONFLICT (window_to) DO UPDATE SET "
                "bullets = ?, status = ?, note = ?, signals_analysed = ?, generated_at = ?",
                (window_from, window_to, body, outcome.status, outcome.note,
                 outcome.signals_analysed, _now(),
                 body, outcome.status, outcome.note, outcome.signals_analysed, _now()),
            )
    except Exception as exc:  # noqa: BLE001 - never fail a pipeline run over this
        log.warning("pulse: could not store the result (%s)", exc)


def load_pulse(window_from: str, window_to: str,
               target: str | Path | None = None) -> dict[str, Any] | None:
    """The Market Pulse for a digest window, or None if there is none to show.

    A *range* over the window, not a lookup by key, and both halves of that
    matter:

    `window_to` alone as a key broke the moment the pipeline ran twice in one
    week. The second run creates newer captures, so the current `window_to`
    moves to the new scrape — and if that run's generation failed, the perfectly
    good pulse from the first run became unreachable under its older key. The
    section vanished because of a *later* failure, which is not what "no
    fallback" was meant to mean.

    Bounding it by `window_from` is what stops the other failure: without a
    lower bound this would happily serve last month's pulse as this week's read.
    A pulse only counts if it was generated for a scrape inside the window being
    rendered.

    Failed rows never match — `status` is filtered here — so a failed run leaves
    the previous good pulse for the same window in place rather than displacing
    it. The reason for the failure stays in the table either way.
    """
    try:
        with connect(target, readonly=True) as conn:
            row = conn.execute(
                "SELECT bullets, status, note, signals_analysed, generated_at "
                "FROM digest_pulse "
                "WHERE status = ? AND window_to >= ? AND window_to <= ? "
                # Newest scrape first; for one scrape, the newest attempt.
                "ORDER BY window_to DESC, generated_at DESC LIMIT 1",
                (STATUS_GENERATED, window_from, window_to),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.debug("pulse: could not read (%s)", exc)
        return None

    if row is None or not row["bullets"]:
        return None
    try:
        bullets = json.loads(row["bullets"])
    except (TypeError, ValueError):
        return None
    if not bullets:
        return None
    return {
        "bullets": bullets,
        "signalsAnalysed": int(row["signals_analysed"] or 0),
        "generatedAt": row["generated_at"],
        "note": row["note"],
    }
