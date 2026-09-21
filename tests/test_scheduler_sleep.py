"""The scheduler lets the database sleep.

Neon suspends a compute after five minutes without activity. The loop read the
schedule every 60 seconds, so the database never went five minutes without a
query and ran all month, using up the free plan's compute by itself. These pin
the replacement: sleep until the next run, wake early only for the refresh or
an administrator's change, and hold no idle connection open.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import api.scheduler as sched_mod
from loader import db
from loader.schedule import Schedule

SYD = ZoneInfo("Australia/Sydney")


def monday_five() -> Schedule:
    return Schedule(enabled=True, day_of_week=0, hour=5, minute=0, timezone="Australia/Sydney")


def at(*args) -> datetime:
    return datetime(*args, tzinfo=SYD).astimezone(timezone.utc)


def test_far_from_a_run_the_loop_sleeps_for_the_whole_refresh():
    """Wednesday afternoon: the next run is days away, so the only reason to
    wake before then is the six-hourly refresh."""
    wait = sched_mod._seconds_until_next_check(monday_five(), at(2026, 9, 2, 15, 0))
    assert wait == sched_mod.REFRESH_SECONDS


def test_close_to_a_run_the_loop_wakes_just_after_it():
    wait = sched_mod._seconds_until_next_check(monday_five(), at(2026, 9, 7, 4, 50))
    assert wait == 10 * 60 + sched_mod.DUE_MARGIN_SECONDS


def test_a_paused_schedule_only_wakes_for_the_refresh():
    paused = Schedule(enabled=False, day_of_week=0, hour=5, minute=0, timezone="Australia/Sydney")
    assert sched_mod._seconds_until_next_check(paused, at(2026, 9, 7, 4, 59)) == sched_mod.REFRESH_SECONDS


def test_the_refresh_leaves_the_database_asleep_most_of_the_day():
    """Neon needs five quiet minutes to suspend. Four reads a day leaves it
    asleep for all but about twenty minutes; the old minute-by-minute read never
    left it five."""
    assert sched_mod.REFRESH_SECONDS >= 60 * 60
    assert 24 * 60 * 60 / sched_mod.REFRESH_SECONDS * 5 <= 60, "at most an hour awake a day"


def _run_loop_briefly(monkeypatch, schedule, *, poke: bool) -> int:
    """Start the loop, optionally announce a change, and count schedule reads."""
    reads = []
    monkeypatch.setattr(sched_mod, "get_schedule", lambda *a, **k: reads.append(1) or schedule)

    async def quiet_tick(sched=None):
        return None

    monkeypatch.setattr(sched_mod, "_tick", quiet_tick)
    # Nothing due for an hour, so only a change should wake it.
    monkeypatch.setattr(sched_mod, "_seconds_until_next_check", lambda s, now: 3600.0)

    async def scenario():
        task = asyncio.create_task(sched_mod._loop())
        for _ in range(50):
            await asyncio.sleep(0.01)
            if reads:
                break
        if poke:
            sched_mod.notify_changed()
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    return len(reads)


def test_between_checks_the_loop_reads_nothing(monkeypatch):
    assert _run_loop_briefly(monkeypatch, monday_five(), poke=False) == 1


def test_saving_the_schedule_wakes_the_loop_at_once(monkeypatch):
    assert _run_loop_briefly(monkeypatch, monday_five(), poke=True) == 2


def test_announcing_a_change_with_no_scheduler_running_is_harmless(monkeypatch):
    monkeypatch.setattr(sched_mod, "_wake", None)
    monkeypatch.setattr(sched_mod, "_wake_loop", None)
    sched_mod.notify_changed()  # must not raise


def test_the_admin_endpoint_announces_a_saved_schedule(monkeypatch, tmp_path):
    from api import admin_api

    told = []
    monkeypatch.setattr(sched_mod, "notify_changed", lambda: told.append(1))
    monkeypatch.setattr(admin_api, "set_schedule", lambda **kw: monday_five())
    monkeypatch.setattr(admin_api, "_schedule_payload", lambda s: {"ok": True})

    admin_api.write_schedule({"enabled": True, "dayOfWeek": 0, "hour": 5, "minute": 0,
                              "timezone": "Australia/Sydney"}, user={"email": "boss@x.com"})
    assert told == [1]


def test_no_connection_is_held_open_while_idle():
    """A pooled minimum is kept alive and replaced as it ages, and each
    reconnect wakes a suspended Neon compute."""
    assert db.POOL_MIN_SIZE == 0
    assert db.POOL_MAX_IDLE_SECONDS < 300, "idle connections close before Neon's five minutes"
