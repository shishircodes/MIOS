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

#: How often to ask. A minute is far finer than a weekly schedule needs, but it
#: is what makes a change in the Admin panel feel immediate, and the check is a
#: single indexed read.
TICK_SECONDS = 60

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
            note=None,
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


async def _tick() -> None:
    """One check. Never raises: the loop must outlive a bad week."""
    try:
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
    sched = await asyncio.to_thread(get_schedule)
    upcoming = next_due(sched, datetime.now(timezone.utc))
    log.info("scheduler: watching %s; next run %s",
             sched.describe(), upcoming.isoformat() if upcoming else "never (paused)")
    while True:
        await _tick()
        await asyncio.sleep(TICK_SECONDS)


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
