"""When the pipeline runs by itself, and whether it is due right now.

Two halves, deliberately separated:

* **The decision** — `next_due`, `due_occurrence` — is pure. It takes a
  schedule, a clock reading and nothing else, and returns an answer. No
  database, no `datetime.now()`. That is what makes a weekly schedule testable
  without waiting a week, and it means the ticker in the API, an administrator
  pressing "Run now", and any external cron all ask the same question.

* **The storage** — the rest of this module — is the settings row and the run
  history that decision is checked against.

Three things shape it:

**Catch-up, not "fire at 05:00 exactly".** Every deploy restarts the API
container. A scheduler that only fires when the current minute equals the
scheduled minute silently skips the week whenever a restart lands on it. So the
question asked each tick is "has the due time passed, and has nothing run for
it?" — answered from the database, and therefore surviving restarts.

**A grace window.** Catch-up must not mean "run Monday's scrape on Friday". An
occurrence older than `DEFAULT_GRACE_HOURS` is abandoned rather than run late,
because a digest labelled "this week" assembled on Friday misrepresents its own
window.

**An IANA timezone, not an offset.** "Early Monday morning" has to keep meaning
that across daylight saving.

This lives in `loader` for the same reason `source_settings` does: the pipeline
side reads it and the API writes it, and `api` already imports `pipeline`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loader.db import connect

log = logging.getLogger(__name__)

#: Monday, 05:00, Sydney. Early enough that the digest is waiting when the week
#: starts; late enough to clear the 02:00-03:00 daylight-saving gap, where a
#: local time can simply not exist.
DEFAULT_DAY = 0
DEFAULT_HOUR = 5
DEFAULT_MINUTE = 0
DEFAULT_TIMEZONE = "Australia/Sydney"

#: How late a missed occurrence may still be run. Beyond this the week is
#: skipped: a late digest misrepresents the window it claims to cover.
DEFAULT_GRACE_HOURS = 12

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday")


class ScheduleError(ValueError):
    """An invalid schedule. The message reaches the administrator."""


@dataclass(frozen=True)
class Schedule:
    enabled: bool = True
    day_of_week: int = DEFAULT_DAY
    hour: int = DEFAULT_HOUR
    minute: int = DEFAULT_MINUTE
    timezone: str = DEFAULT_TIMEZONE
    changed_by: str | None = None
    changed_at: str | None = None

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def describe(self) -> str:
        """Human phrasing, for the panel and the logs."""
        return (f"{DAY_NAMES[self.day_of_week]} at "
                f"{self.hour:02d}:{self.minute:02d} {self.timezone}")


def validate(day_of_week: int, hour: int, minute: int, tz_name: str) -> None:
    """Reject a schedule when it is set, not when it fails to fire."""
    if not 0 <= day_of_week <= 6:
        raise ScheduleError("Day must be 0 (Monday) through 6 (Sunday).")
    if not 0 <= hour <= 23:
        raise ScheduleError("Hour must be between 0 and 23.")
    if not 0 <= minute <= 59:
        raise ScheduleError("Minute must be between 0 and 59.")
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ScheduleError(
            f"'{tz_name}' is not a known timezone. Use an IANA name such as "
            "Australia/Sydney or Pacific/Port_Moresby."
        ) from exc


# ---------------------------------------------------------------------------
# The decision. Pure: everything it needs is an argument.
# ---------------------------------------------------------------------------


def _occurrence_on(sched: Schedule, local_day: datetime) -> datetime:
    """The scheduled instant on a given local date, returned as UTC.

    Built in local time and then converted, which is the only order that keeps
    "05:00 Monday" at 05:00 through a daylight-saving change.
    """
    aware = local_day.replace(hour=sched.hour, minute=sched.minute,
                              second=0, microsecond=0)
    return aware.astimezone(timezone.utc)


def next_due(sched: Schedule, after: datetime) -> datetime | None:
    """The first scheduled instant strictly after `after`, in UTC.

    `None` when the schedule is switched off — an administrator pausing the
    automation is a legitimate state, not an error.
    """
    if not sched.enabled:
        return None

    local = after.astimezone(sched.zone)
    # Walk forward from today. Eight candidates covers "later today" through
    # "this day next week" without special-casing the wrap.
    for offset in range(0, 9):
        day = local + timedelta(days=offset)
        if day.weekday() != sched.day_of_week:
            continue
        candidate = _occurrence_on(sched, day)
        if candidate > after:
            return candidate
    return None


def previous_due(sched: Schedule, before: datetime) -> datetime | None:
    """The most recent scheduled instant at or before `before`, in UTC."""
    if not sched.enabled:
        return None

    local = before.astimezone(sched.zone)
    for offset in range(0, 9):
        day = local - timedelta(days=offset)
        if day.weekday() != sched.day_of_week:
            continue
        candidate = _occurrence_on(sched, day)
        if candidate <= before:
            return candidate
    return None


def due_occurrence(
    sched: Schedule,
    now: datetime,
    *,
    grace_hours: int = DEFAULT_GRACE_HOURS,
) -> datetime | None:
    """The occurrence that ought to have run by now and is still worth running.

    Returns the scheduled instant rather than a boolean, because that instant is
    what gets written to `pipeline_runs.due_at` — the record that stops the same
    occurrence running twice. A bare "yes" would leave the caller to recompute
    it, and possibly disagree.
    """
    previous = previous_due(sched, now)
    if previous is None:
        return None
    if now - previous > timedelta(hours=grace_hours):
        # Missed by too much. Running it now would produce a digest whose window
        # does not match the week it claims to cover.
        return None
    return previous


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_schedule(target: str | Path | None = None) -> Schedule:
    """The stored schedule, or the default when nobody has set one.

    Falls back to the default on an unreadable table rather than raising: the
    caller is a background loop, and a database hiccup should not be the thing
    that permanently stops the automation.
    """
    try:
        with connect(target, readonly=True) as conn:
            row = conn.execute(
                "SELECT enabled, day_of_week, hour, minute, timezone, "
                "changed_by, changed_at FROM schedule_settings WHERE id = 1"
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 - the table may not exist yet
        log.warning("schedule: could not read settings (%s) — using the default", exc)
        return Schedule()

    if row is None:
        return Schedule()
    return Schedule(
        enabled=bool(row["enabled"]),
        day_of_week=int(row["day_of_week"]),
        hour=int(row["hour"]),
        minute=int(row["minute"]),
        timezone=str(row["timezone"]),
        changed_by=row["changed_by"],
        changed_at=row["changed_at"],
    )


def set_schedule(
    *,
    enabled: bool,
    day_of_week: int,
    hour: int,
    minute: int,
    tz_name: str,
    changed_by: str,
    target: str | Path | None = None,
) -> Schedule:
    """Store the schedule. Takes effect on the next tick, within a minute."""
    validate(day_of_week, hour, minute, tz_name)
    stamp = _now()

    with connect(target) as conn:
        conn.execute(
            "INSERT INTO schedule_settings "
            "(id, enabled, day_of_week, hour, minute, timezone, changed_by, changed_at) "
            "VALUES (1,?,?,?,?,?,?,?) "
            "ON CONFLICT (id) DO UPDATE SET "
            "enabled = ?, day_of_week = ?, hour = ?, minute = ?, timezone = ?, "
            "changed_by = ?, changed_at = ?",
            (1 if enabled else 0, day_of_week, hour, minute, tz_name, changed_by, stamp,
             1 if enabled else 0, day_of_week, hour, minute, tz_name, changed_by, stamp),
        )

    sched = get_schedule(target)
    log.info("schedule: %s set the run to %s (%s)", changed_by, sched.describe(),
             "on" if enabled else "paused")
    return sched
