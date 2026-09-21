"""The loop that runs the weekly pipeline without anybody pressing anything.

Why this lives inside the API process rather than in cron
---------------------------------------------------------
The requirement is not only "run it on Monday" — it is "let an administrator
change when". That rules out the obvious answers:

* **GitHub Actions `schedule:`** puts the cron expression in a YAML file, so
  changing the time means a commit and a deploy. It also stops firing after 60
  days without repository activity, which for a university project means the
  automation dies quietly some weeks after the semester ends.
* **A host crontab or systemd timer** needs SSH to change.

Both could be adapted by having them run every hour and asking the database
whether *this* hour is the one — and `loader.schedule.due_occurrence` is
deliberately pure so that either still can. But then the schedule an
administrator sets is a filter on somebody else's clock, and the finest
resolution offered is whatever that clock ticks at.

So the process that owns the setting also owns the timer.

What the loop is careful about
------------------------------
**It asks a question about state, not about the time.** "Has the due moment
passed with nothing run for it?" rather than "is it 05:00 right now?" — the
second form misses the week whenever a deploy restarts the container on the
scheduled minute.

**It does not block the event loop.** A full cycle is minutes of scraping and
Gemini calls; run inline it would stall every HTTP request in the process.

**It never lets a failure stop the loop.** A raised exception inside a bare
`while True` kills the task silently, and the next anyone hears of it is a
month of missing digests.

**It lets the database sleep.** Neon suspends a compute after five minutes
without activity and bills only while it is awake. The loop used to read the
schedule every 60 seconds, so the database never went five minutes without a
query: it ran all month and used up the free plan's monthly compute on its own.
The loop now reads the schedule, works out the next run, and sleeps until then
— waking earlier only every `REFRESH_SECONDS` as a safety net, or at once when
an administrator saves a new schedule (`notify_changed`). Between those moments
it sends the database nothing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from config.settings import settings
from loader import run_log
from loader.schedule import due_occurrence, get_schedule, next_due

log = logging.getLogger("api.scheduler")

#: The longest the loop sleeps without re-reading the schedule. A change made in
#: the Admin panel wakes it at once, so this only covers a change it was not told
#: about (another process, or a direct database edit) and clock drift. Each
#: re-read wakes the database for about five minutes, so four a day costs a
#: fraction of an hour of compute a day; the old minute-by-minute read cost all
#: of it.
REFRESH_SECONDS = 6 * 60 * 60

#: Woken a few seconds after the due instant rather than on it, so a timer that
#: fires a hair early does not read the schedule, find nothing due yet, and go
#: back to sleep for a week.
DUE_MARGIN_SECONDS = 5

#: How often a run in flight says it is still alive. Comfortably inside
#: `run_log.STALE_LEASE_MINUTES`, so a healthy run is never mistaken for a dead
#: one.
HEARTBEAT_SECONDS = 60


async def _beat(run_id: str) -> None:
    """Refresh the lease until cancelled."""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await asyncio.to_thread(run_log.heartbeat, run_id)
    except asyncio.CancelledError:
        pass


async def execute_run(run_id: str) -> dict[str, Any]:
    """Run one cycle for a lease already taken, then close it.

    Separate from claiming because the two happen at different moments for a
    manual run: the Admin panel claims synchronously, so a refusal is an HTTP
    409 the administrator sees, and only then hands the work to the background.
    """
    # Imported here, not at module scope: `pipeline.live` pulls in the scrapers
    # and the Gemini client, and the API should not pay that at import time when
    # the scheduler is switched off.
    from pipeline.live import run_live_cycle

    beat = asyncio.create_task(_beat(run_id))
    try:
        # The run id goes down with it: the cycle stamps every signal with it
        # and stores the digest under it. Without this the cycle would try to
        # open a second run and be refused by the lease this one holds.
        summary = await asyncio.to_thread(lambda: run_live_cycle(run_id=run_id))
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        await asyncio.to_thread(
            run_log.finish, run_id, status=run_log.STATUS_FAILED, note=repr(exc)
        )
        log.exception("scheduler: run %s failed", run_id)
        raise
    else:
        await asyncio.to_thread(
            run_log.finish, run_id,
            status=run_log.STATUS_OK,
            collected=int(summary.get("scraped") or 0),
            # Why a run that finished is not the whole story, when it is not.
            note=summary.get("note"),
        )
        return {"runId": run_id, **summary}
    finally:
        beat.cancel()


async def run_pipeline(
    *,
    trigger: str,
    due_at: datetime | None = None,
    started_by: str | None = None,
) -> dict[str, Any]:
    """Claim the lease and run a cycle. Used by the ticker.

    Raises `run_log.RunInProgress` / `AlreadyRan` when the lease cannot be
    taken; the caller decides what to say about that.
    """
    run_id = await asyncio.to_thread(
        lambda: run_log.claim(trigger=trigger, due_at=due_at, started_by=started_by)
    )
    return await execute_run(run_id)


#: Set when an administrator saves the schedule, so the sleeping loop re-reads
#: it at once. Created by the loop itself, on the loop it belongs to.
_wake: asyncio.Event | None = None
_wake_loop: asyncio.AbstractEventLoop | None = None


def notify_changed() -> None:
    """Tell the loop the schedule changed. Safe from any thread.

    The Admin endpoint that saves the schedule runs in a worker thread, and an
    `asyncio.Event` must be set from the loop that owns it — hence
    `call_soon_threadsafe`. Does nothing when the scheduler is not running here.
    """
    if _wake is None or _wake_loop is None:
        return
    try:
        _wake_loop.call_soon_threadsafe(_wake.set)
    except RuntimeError:  # the loop has closed: nothing is waiting
        pass


def _seconds_until_next_check(sched: Any, now: datetime) -> float:
    """How long the loop may sleep: until the next run, or the refresh, if sooner."""
    upcoming = next_due(sched, now)
    if upcoming is None:  # paused: nothing to wake for but the refresh
        return float(REFRESH_SECONDS)
    until_due = (upcoming - now).total_seconds() + DUE_MARGIN_SECONDS
    return max(1.0, min(float(REFRESH_SECONDS), until_due))


async def _tick(sched: Any = None) -> None:
    """One check. Never raises: the loop must outlive a bad week.

    `sched` is the schedule the loop has just read; without one, it is read
    here. The database is only asked whether a run happened when one is due.
    """
    try:
        if sched is None:
            sched = await asyncio.to_thread(get_schedule)
        now = datetime.now(timezone.utc)
        due = due_occurrence(sched, now)
        if due is None:
            return

        if await asyncio.to_thread(run_log.has_run_for, due):
            return

        log.info("scheduler: %s is due (%s) — starting", sched.describe(), due.isoformat())
        await run_pipeline(trigger=run_log.TRIGGER_SCHEDULE, due_at=due)
    except (run_log.RunInProgress, run_log.AlreadyRan) as exc:
        # Both are ordinary: another worker got there first, or a manual run is
        # already going. Try again next tick.
        log.info("scheduler: not starting (%s)", exc)
    except Exception:  # noqa: BLE001
        log.exception("scheduler: tick failed; continuing")


async def _loop() -> None:
    global _wake, _wake_loop
    _wake = asyncio.Event()
    _wake_loop = asyncio.get_running_loop()
    described = None
    while True:
        try:
            sched = await asyncio.to_thread(get_schedule)
        except Exception:  # noqa: BLE001 - get_schedule fails soft; this is belt and braces
            log.exception("scheduler: could not read the schedule; trying again later")
            sched = None

        if sched is not None:
            await _tick(sched)
            now = datetime.now(timezone.utc)
            wait = _seconds_until_next_check(sched, now)
            upcoming = next_due(sched, now)
            summary = (sched.describe(), upcoming.isoformat() if upcoming else "never (paused)")
            if summary != described:
                log.info("scheduler: watching %s; next run %s", *summary)
                described = summary
        else:
            wait = float(REFRESH_SECONDS)

        # Asleep until the next run, the refresh, or an administrator's change —
        # whichever comes first. Nothing reaches the database in between.
        try:
            await asyncio.wait_for(_wake.wait(), timeout=wait)
            log.info("scheduler: the schedule changed; re-reading it")
        except asyncio.TimeoutError:
            pass
        _wake.clear()


def start(app_state: Any) -> asyncio.Task | None:
    """Start the ticker, unless this process is not the one that should.

    Returns the task so the lifespan can cancel it on shutdown; `None` when the
    scheduler is off, which is the default and what every developer's local API
    gets.
    """
    if not settings.scheduler_enabled:
        log.info("scheduler: disabled (set SCHEDULER_ENABLED=true on the server "
                 "that should run the weekly pipeline)")
        return None

    task = asyncio.create_task(_loop())
    app_state.scheduler_task = task
    return task


async def stop(task: asyncio.Task | None) -> None:
    """Cancel the ticker and wait for it to notice."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
