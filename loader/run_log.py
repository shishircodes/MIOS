"""A record of every pipeline run, and the lease that stops two overlapping.

This is not a log kept for its own sake. It does three jobs, and each of them is
something the scheduler cannot do without:

**It remembers which occurrence ran.** `due_at` holds the scheduled instant a
run satisfied, so "has Monday 05:00 already happened?" is answered from the
database rather than from a timer that a redeploy resets. A unique index on
`due_at` makes that guarantee the database's rather than the code's.

**It stops two runs overlapping.** A scrape takes minutes and spends Gemini
quota against a daily cap of 20 calls. Two at once would double-spend it and
interleave writes into `signals`. A `running` row is the lease.

**It explains a quiet week.** "Why is there no digest?" is answerable — the run
failed, or was never due, or is still going.

The lease is heartbeated rather than held for a fixed duration, so a container
killed mid-scrape leaves a lease that visibly goes stale instead of blocking
every later run forever.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)

TRIGGER_SCHEDULE = "schedule"
TRIGGER_MANUAL = "manual"
TRIGGER_CLI = "cli"

STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_FAILED = "failed"

#: A run whose heartbeat is older than this is presumed dead. Generous, because
#: reclaiming a lease from a run that is merely slow would start the second
#: scrape this table exists to prevent.
STALE_LEASE_MINUTES = 90


class RunInProgress(RuntimeError):
    """Another run holds the lease. The message reaches the administrator."""


class AlreadyRan(RuntimeError):
    """This scheduled occurrence has already been run."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(when: datetime | None = None) -> str:
    """A UTC ISO string, whatever zone the caller handed us.

    Normalised rather than trusted, because `due_at` is compared and made
    unique as *text*: the same instant spelled `2026-09-07T05:00:00+10:00` and
    `2026-09-06T19:00:00+00:00` would look like two different occurrences, and
    Monday would run twice. The same applies to `heartbeat_at`, which is
    compared against a cutoff by string ordering.

    Nothing passes a non-UTC value today — `due_occurrence` converts before
    returning — but that is one call site away from being untrue, and the
    failure would be a duplicate scrape rather than an error.
    """
    when = when or _now()
    if when.tzinfo is None:
        # A naive datetime has no instant. Refusing beats guessing: assuming UTC
        # would silently shift the schedule when the guess is wrong.
        raise ValueError("timestamps must carry a timezone")
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "trigger": r["trigger"],
        "dueAt": r["due_at"],
        "startedAt": r["started_at"],
        "finishedAt": r["finished_at"],
        "status": r["status"],
        "startedBy": r["started_by"],
        "collected": r["collected"],
        "note": r["note"],
    }


def active_run(target: str | Path | None = None) -> dict[str, Any] | None:
    """The run currently in flight, if its lease is still live.

    A `running` row with a stale heartbeat is not returned: the process that
    wrote it is gone, and treating it as live would block the pipeline until
    somebody edited the table by hand.
    """
    cutoff = _stamp(_now() - timedelta(minutes=STALE_LEASE_MINUTES))
    try:
        with connect(target, readonly=True) as conn:
            r = conn.execute(
                "SELECT * FROM pipeline_runs WHERE status = ? AND heartbeat_at >= ? "
                "ORDER BY started_at DESC LIMIT 1",
                (STATUS_RUNNING, cutoff),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 - the table may not exist yet
        log.warning("run_log: could not read the active run (%s)", exc)
        return None
    return _row(r) if r else None


def has_run_for(due_at: datetime, target: str | Path | None = None) -> bool:
    """Whether this scheduled occurrence has already been claimed.

    True for a failed run too. A failure that repeats every minute for the rest
    of the week is worse than a missed week, and the failure is visible in the
    history either way.
    """
    try:
        with connect(target, readonly=True) as conn:
            r = conn.execute(
                "SELECT 1 FROM pipeline_runs WHERE due_at = ? LIMIT 1",
                (_stamp(due_at),),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        # Fail closed here, unlike everywhere else in this feature: if we cannot
        # tell whether Monday already ran, not running is the safe answer. The
        # cost of a missed week is a late digest; the cost of a wrong "no" is an
        # unbounded loop of scrapes burning the Gemini quota.
        log.warning("run_log: could not check occurrence %s (%s) — assuming it ran",
                    due_at, exc)
        return True
    return r is not None


def claim(
    *,
    trigger: str,
    due_at: datetime | None = None,
    started_by: str | None = None,
    target: str | Path | None = None,
) -> str:
    """Take the lease and open a run. Returns the run id.

    Raises rather than returning a flag, because every caller has something
    specific to say: the ticker logs and waits, the Admin panel shows the
    message to whoever pressed the button.
    """
    running = active_run(target)
    if running is not None:
        raise RunInProgress(
            f"A pipeline run started at {running['startedAt']} is still going."
        )
    if due_at is not None and has_run_for(due_at, target):
        raise AlreadyRan(f"The run due at {_stamp(due_at)} has already happened.")

    run_id = uuid.uuid4().hex
    now = _stamp()
    try:
        with connect(target) as conn:
            conn.execute(
                "INSERT INTO pipeline_runs "
                "(id, trigger, due_at, started_at, heartbeat_at, status, started_by) "
                "VALUES (?,?,?,?,?,?,?)",
                (run_id, trigger, _stamp(due_at) if due_at else None,
                 now, now, STATUS_RUNNING, started_by),
            )
    except Exception as exc:  # noqa: BLE001
        # The unique index on `due_at` is what turns the check above from a
        # race into a guarantee: two processes can both read "not yet run", but
        # only one insert survives.
        if due_at is not None:
            raise AlreadyRan(
                f"The run due at {_stamp(due_at)} was claimed by another process."
            ) from exc
        raise

    log.info("run_log: opened %s run %s%s", trigger, run_id,
             f" for {_stamp(due_at)}" if due_at else "")
    return run_id


def heartbeat(run_id: str, target: str | Path | None = None) -> None:
    """Say the run is still alive. Never raises — a missed beat is not fatal."""
    try:
        with connect(target) as conn:
            conn.execute("UPDATE pipeline_runs SET heartbeat_at = ? WHERE id = ?",
                         (_stamp(), run_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("run_log: heartbeat failed for %s (%s)", run_id, exc)


def finish(
    run_id: str,
    *,
    status: str,
    collected: int | None = None,
    note: str | None = None,
    target: str | Path | None = None,
) -> None:
    """Close the run and release the lease."""
    try:
        with connect(target) as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status = ?, finished_at = ?, "
                "collected = ?, note = ? WHERE id = ?",
                (status, _stamp(), collected, (note or "")[:500] or None, run_id),
            )
    except Exception as exc:  # noqa: BLE001
        # Worth shouting about: the lease is now stuck until it goes stale.
        log.error("run_log: could not close run %s (%s)", run_id, exc)
    else:
        log.info("run_log: %s finished %s", run_id, status)


def recent(limit: int = 10, target: str | Path | None = None) -> list[dict[str, Any]]:
    """The most recent runs, newest first, for the Admin panel."""
    try:
        with connect(target, readonly=True) as conn:
            rows = conn.execute(
                "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?",
                (max(1, min(limit, 50)),),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("run_log: could not read history (%s)", exc)
        return []
    return [_row(r) for r in rows]
