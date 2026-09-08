"""Stored weekly digests: one per pipeline run, kept after newer runs arrive.

Two problems this replaces, both consequences of computing the digest from a
rolling seven-day window on every page load:

**Runs were blended.** A digest built the day after a Monday scrape showed the
previous Monday's signals beside that one, under a heading claiming to cover a
single week. The figures were arithmetically correct and told you about two
collections at once.

**There was no past.** A new run changed what the one page said. Last week's
digest — the thing that was sent to Slack and possibly acted on — could not be
read back afterwards.

So a digest is written once, when the run that produced it finishes, and kept.

**The payload is stored, not re-derived.** The obvious alternative is to keep
only a run id and rebuild the digest on demand from that run's signals. It would
save a little space and always reflect the current code. It would also mean that
re-classifying a signal, editing the watchlist, or adding a competitor silently
rewrites a digest published weeks ago. What is stored here is what was
published; that is the point of an archive.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)

#: Archive listings are for choosing which past digest to read, so they carry
#: the labels and counts and never the payloads.
DEFAULT_LIST_LIMIT = 26


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _summary(row: Any) -> dict[str, Any]:
    """One archive entry without its payload."""
    return {
        "runId": row["run_id"],
        "windowFrom": row["window_from"],
        "windowTo": row["window_to"],
        "generatedAt": row["generated_at"],
        "signalCount": int(row["signal_count"] or 0),
    }


def save_digest(
    *,
    run_id: str,
    payload: dict[str, Any],
    window_from: str,
    window_to: str,
    digest_text: str | None = None,
    target: str | Path | None = None,
) -> None:
    """Store this run's digest, replacing any earlier attempt for the same run.

    Upserts rather than inserting, so re-running a cycle for the same run
    corrects its digest instead of failing or leaving two. The run id is the
    primary key precisely so that "one run, one digest" is the database's
    guarantee and not a convention the code has to remember.
    """
    # The collected total, not the length of `signals` — that list is capped at
    # MAX_SIGNALS_SHOWN for display, so using it would label every week in the
    # archive "40 signals" however much was actually gathered.
    collection = payload.get("collection") or {}
    signal_count = int(collection.get("collected") or len(payload.get("signals") or []))
    body = json.dumps(payload, separators=(",", ":"))
    stamp = _now()

    with connect(target) as conn:
        conn.execute(
            "INSERT INTO digests "
            "(run_id, window_from, window_to, generated_at, signal_count, payload, digest_text) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (run_id) DO UPDATE SET "
            "window_from = ?, window_to = ?, generated_at = ?, signal_count = ?, "
            "payload = ?, digest_text = ?",
            (run_id, window_from, window_to, stamp, signal_count, body, digest_text,
             window_from, window_to, stamp, signal_count, body, digest_text),
        )
    log.info("digest_archive: stored digest for run %s (%d signals)", run_id, signal_count)


def _load_one(sql: str, params: tuple, target) -> dict[str, Any] | None:
    try:
        with connect(target, readonly=True) as conn:
            row = conn.execute(sql, params).fetchone()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.warning("digest_archive: could not read (%s)", exc)
        return None
    if row is None:
        return None

    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError) as exc:
        # A row that cannot be parsed is worse than no row: the dashboard would
        # render a broken digest instead of falling back to a live one.
        log.error("digest_archive: run %s has an unreadable payload (%s)", row["run_id"], exc)
        return None

    payload["archived"] = _summary(row)
    payload["digestText"] = row["digest_text"]
    _relabel(payload)
    return payload


def _relabel(payload: dict[str, Any]) -> None:
    """Re-derive the headings from the timestamps the payload already carries.

    A digest is archived as a snapshot, and its *figures* must stay exactly as
    published — a later re-classification or watchlist edit must not quietly
    rewrite what a past week reported. A heading is not a figure. It is a
    rendering of `collectedFrom`/`collectedTo`, both of which are stored, so
    deriving it on read costs nothing and keeps every archived week consistent
    with how dates are shown today.

    That distinction was learned the hard way twice. The Market Pulse had to be
    repaired row by row because it was frozen into the payload, and the same
    week the headings were found to be a day out — named in UTC when the run
    fires at 05:00 Australia/Sydney — with every stored digest carrying the
    wrong weekday as a baked string. Fixing the formatter changed nothing on
    screen, because what is served is the archive.

    Anything derived from stored timestamps belongs here rather than in the
    stored bytes, so the next formatting change needs no migration at all.

    `generatedAt` is deliberately NOT re-derived: it records when the digest was
    produced, which is a fact about the run and not a rendering of anything in
    the payload.
    """
    from datetime import datetime as _dt

    raw_to, raw_from = payload.get("collectedTo"), payload.get("collectedFrom")
    if not raw_to:
        # Synthetic or pre-archive rows carry no collection span; their stored
        # label ("Sample dataset") is the only one available and stays.
        return
    try:
        end = _dt.fromisoformat(str(raw_to))
        start = _dt.fromisoformat(str(raw_from)) if raw_from else end
    except ValueError:
        log.warning("digest_archive: unparsable collection span; keeping stored labels")
        return

    from api.digest_service import _collection_label, _local

    payload["weekLabel"] = _collection_label(start, end)
    payload["week"] = _local(end).strftime("WEEK %d %b %Y").upper()


def latest_digest(target: str | Path | None = None) -> dict[str, Any] | None:
    """The most recent stored digest, or None before any run has produced one."""
    return _load_one(
        "SELECT * FROM digests ORDER BY window_to DESC, generated_at DESC LIMIT 1",
        (), target)


def load_digest(run_id: str, target: str | Path | None = None) -> dict[str, Any] | None:
    """One specific past digest."""
    return _load_one("SELECT * FROM digests WHERE run_id = ?", (run_id,), target)


def list_digests(limit: int = DEFAULT_LIST_LIMIT,
                 target: str | Path | None = None) -> list[dict[str, Any]]:
    """Every stored digest, newest first, without payloads."""
    try:
        with connect(target, readonly=True) as conn:
            rows = conn.execute(
                "SELECT run_id, window_from, window_to, generated_at, signal_count "
                "FROM digests ORDER BY window_to DESC, generated_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("digest_archive: could not list (%s)", exc)
        return []
    return [_summary(r) for r in rows]
