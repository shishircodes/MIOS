"""What the BD team did with a match, so the scoring can eventually be judged.

Every weight in `push/matcher.py` is judgement. The module says so plainly:
nobody has been placed through this, so nothing has been calibrated against
what actually predicts a placement. That is an honest position to start from
and a bad one to stay in, and it cannot be left without data that does not yet
exist.

This records that data. A consultant marks a ranked company as contacted, not
relevant, or placed, and the row keeps the score **as it stood at that moment**.

Freezing the score is the point, not an optimisation. Recomputing it later
would judge a decision against a model that did not exist when the decision was
made, over signals collected since — a model marking its own homework, with the
answers written afterwards. Every such row would confirm whatever the current
weights believe.

**What this deliberately does not do is report accuracy yet.** `summary()`
returns counts and says how far they are from being enough to conclude
anything. A precision figure computed over four outcomes is not a small
measurement, it is a wrong one, and putting it on a screen would repeat exactly
the mistake the dashboard, the velocity table and Mode Push momentum each had
to be fixed for: presenting the absence of evidence as a finding.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)

#: What can be recorded. Kept deliberately short: a vocabulary the team will not
#: use produces rows nobody trusts, and every extra verb splits an already thin
#: sample. "Contacted" is the decision the score is actually trying to inform.
OUTCOMES: dict[str, str] = {
    "contacted": "Approached this company about the candidate",
    "not_relevant": "Not worth approaching — the score was wrong",
    "placed": "The candidate was placed here",
}

#: Below this many outcomes, no rate is reported. Not a significance threshold —
#: nothing here is a hypothesis test — but the point below which a single row
#: moves a percentage by more than five points, which is a number that would
#: mislead more than it informs.
MIN_FOR_RATES = 30

#: Bands used to report where a score sat when a decision was taken. Coarse on
#: purpose: with a sample this thin, ten buckets would each hold nothing.
BANDS: tuple[tuple[str, int, int], ...] = (
    ("80-100", 80, 100),
    ("60-79", 60, 79),
    ("40-59", 40, 59),
    ("0-39", 0, 39),
)


class UnknownOutcome(ValueError):
    """Not a verb this records. The message reaches the user."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_DDL = """
CREATE TABLE IF NOT EXISTS match_outcomes (
    outcome_id   TEXT PRIMARY KEY,
    profile_id   TEXT NOT NULL,
    company_name TEXT NOT NULL,
    outcome      TEXT NOT NULL,
    score        INTEGER,
    confidence   TEXT,
    assessable   INTEGER,
    rank_shown   INTEGER,
    note         TEXT,
    recorded_by  TEXT,
    recorded_at  TEXT NOT NULL
)
"""


def _ensure_table(conn) -> None:
    """Created on demand as well as by `schema.sql`.

    The schema is applied by `init_db`, which runs during a pipeline run, so on
    a deployment that has not run since this shipped the first consultant to
    press a button would get an error about a missing table. Same reasoning as
    `loader.credentials`.
    """
    conn.execute(_DDL)


def record(
    profile_id: str,
    company_name: str,
    outcome: str,
    *,
    score: int | None = None,
    confidence: str | None = None,
    assessable: int | None = None,
    rank_shown: int | None = None,
    note: str | None = None,
    recorded_by: str | None = None,
    target: str | Path | None = None,
) -> str:
    """Record one decision. Returns the row id.

    Appends rather than replaces: a company marked contacted and later placed is
    two facts about the same approach, and collapsing them would lose the one
    that matters most. `summary` reads the strongest outcome per pair.
    """
    if outcome not in OUTCOMES:
        raise UnknownOutcome(
            f"'{outcome}' is not an outcome this records. "
            f"Known: {', '.join(sorted(OUTCOMES))}."
        )
    if not profile_id or not company_name:
        raise ValueError("An outcome needs both a profile and a company.")

    outcome_id = uuid.uuid4().hex
    with connect(target) as conn:
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO match_outcomes (outcome_id, profile_id, company_name, outcome, "
            "score, confidence, assessable, rank_shown, note, recorded_by, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (outcome_id, profile_id, company_name, outcome, score, confidence,
             assessable, rank_shown, note, recorded_by, _now()),
        )
    log.info("outcomes: %s marked %s/%s as %s (score %s)",
             recorded_by or "someone", profile_id, company_name, outcome, score)
    return outcome_id


def for_profile(profile_id: str, target: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """The latest outcome per company for one profile, so the UI can show state.

    Fails open to empty: an unreadable table should leave the matches usable,
    not stop them rendering.
    """
    try:
        with connect(target, readonly=True) as conn:
            rows = conn.execute(
                "SELECT company_name, outcome, note, recorded_by, recorded_at "
                "FROM match_outcomes WHERE profile_id = ? "
                "ORDER BY recorded_at",
                (profile_id,),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.warning("outcomes: could not read (%s)", exc)
        return {}
    # Later rows win, so the most recent decision per company is what shows.
    return {str(r["company_name"]): dict(r) for r in rows}


def _band(score: int | None) -> str | None:
    if score is None:
        return None
    for label, low, high in BANDS:
        if low <= score <= high:
            return label
    return None


def summary(target: str | Path | None = None) -> dict[str, Any]:
    """What has been recorded, and whether it is yet enough to conclude anything.

    `readyToCalibrate` is the field that matters. Until it is true, the caller
    must not present a rate: the counts are shown so the team can see the
    sample growing, not so a percentage can be derived from four rows.
    """
    try:
        with connect(target, readonly=True) as conn:
            rows = conn.execute(
                "SELECT profile_id, company_name, outcome, score, confidence "
                "FROM match_outcomes ORDER BY recorded_at").fetchall()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.warning("outcomes: could not summarise (%s)", exc)
        rows = []

    # One decision per profile/company pair: the strongest, since "placed"
    # supersedes "contacted" about the same approach. Counting both would
    # inflate the sample with the same event twice.
    rank = {"not_relevant": 0, "contacted": 1, "placed": 2}
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (str(r["profile_id"]), str(r["company_name"]))
        current = best.get(key)
        if current is None or rank.get(str(r["outcome"]), 0) >= rank.get(str(current["outcome"]), 0):
            best[key] = dict(r)

    decisions = list(best.values())
    counts = {name: sum(1 for d in decisions if d["outcome"] == name) for name in OUTCOMES}
    total = len(decisions)

    by_band = []
    for label, _low, _high in BANDS:
        in_band = [d for d in decisions if _band(d.get("score")) == label]
        by_band.append({
            "band": label,
            "total": len(in_band),
            **{name: sum(1 for d in in_band if d["outcome"] == name) for name in OUTCOMES},
        })

    ready = total >= MIN_FOR_RATES
    return {
        "total": total,
        "counts": counts,
        "byBand": by_band,
        "minimumForRates": MIN_FOR_RATES,
        "readyToCalibrate": ready,
        #: Written for the screen. Until there is a sample, the honest thing to
        #: display is how far off one is — not a rate computed from too little.
        "note": (
            f"{total} decisions recorded. Rates are shown once there are "
            f"{MIN_FOR_RATES}: below that a single outcome moves a percentage by "
            f"more than five points, which would mislead more than it informs."
        ) if not ready else (
            f"{total} decisions recorded — enough to start comparing how scores "
            f"in each band actually performed."
        ),
    }
