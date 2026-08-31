"""Tests for the automatic weekly pipeline run.

The feature is a timer, so almost everything here is about the cases a timer
gets wrong in production and never in development:

* the container restarts on the scheduled minute (deploys do this)
* daylight saving moves the wall clock under a fixed UTC offset
* two processes decide it is Monday at the same instant
* a run dies mid-scrape and leaves its lease behind

None of those need a real clock. `loader.schedule` takes `now` as an argument
precisely so a week, a year and a daylight-saving boundary are all reachable in
a millisecond.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from loader import run_log
from loader.db import connect
from loader.ingest import init_db
from loader.schedule import (
    Schedule,
    ScheduleError,
    due_occurrence,
    get_schedule,
    next_due,
    previous_due,
    set_schedule,
    validate,
)

SYD = ZoneInfo("Australia/Sydney")


@pytest.fixture
def db(tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    path = tmp_path / "sched.db"
    init_db(path, watchlist_path=wl)
    return path


def syd(y, m, d, hh=0, mm=0) -> datetime:
    """A Sydney wall-clock moment, as UTC."""
    return datetime(y, m, d, hh, mm, tzinfo=SYD).astimezone(timezone.utc)


# ---------- the default ----------


def test_the_default_is_early_monday_morning_in_sydney(db):
    sched = get_schedule(db)
    assert sched.describe() == "Monday at 05:00 Australia/Sydney"
    assert sched.enabled


def test_an_unset_schedule_still_has_a_next_run(db):
    """No row must mean the default, not "never". A fresh deployment should
    start running the pipeline without anybody visiting the Admin panel."""
    assert next_due(get_schedule(db), datetime.now(timezone.utc)) is not None


# ---------- finding the next run ----------


def test_the_next_run_is_the_coming_monday(db):
    # Wednesday.
    assert next_due(Schedule(), syd(2026, 9, 2, 9, 0)) == syd(2026, 9, 7, 5, 0)


def test_later_on_the_scheduled_day_rolls_to_next_week(db):
    """05:01 on Monday must not resolve to 05:00 the same morning, or the run
    fires again the moment it finishes."""
    assert next_due(Schedule(), syd(2026, 9, 7, 5, 1)) == syd(2026, 9, 14, 5, 0)


def test_earlier_on_the_scheduled_day_stays_that_morning(db):
    assert next_due(Schedule(), syd(2026, 9, 7, 4, 59)) == syd(2026, 9, 7, 5, 0)


def test_a_paused_schedule_has_no_next_run(db):
    assert next_due(Schedule(enabled=False), syd(2026, 9, 2)) is None


# ---------- daylight saving ----------


def test_the_local_time_holds_across_daylight_saving(db):
    """The bug a stored UTC time would have: the run drifts to 04:00 or 06:00
    local twice a year, and "early Monday morning" quietly stops being true."""
    winter = next_due(Schedule(), syd(2026, 6, 15))   # AEST, UTC+10
    summer = next_due(Schedule(), syd(2026, 12, 15))  # AEDT, UTC+11

    assert winter.astimezone(SYD).hour == 5
    assert summer.astimezone(SYD).hour == 5
    # Same local hour, deliberately different UTC hours — that is the point.
    assert winter.hour != summer.hour


def test_a_schedule_in_another_zone_is_that_zone(db):
    """PNG does not observe daylight saving; the operator might be there."""
    sched = Schedule(timezone="Pacific/Port_Moresby")
    when = next_due(sched, datetime(2026, 9, 2, tzinfo=timezone.utc))
    assert when.astimezone(sched.zone).hour == 5
    assert when.astimezone(sched.zone).weekday() == 0


# ---------- catch-up, which is what restarts break ----------


def test_a_run_missed_by_an_hour_is_still_due(db):
    """Every deploy restarts the API container. A scheduler that only fires when
    the clock reads exactly 05:00 loses the week whenever a restart lands on
    it — so the question is "has it passed?", not "is it now?"."""
    assert due_occurrence(Schedule(), syd(2026, 9, 7, 6, 0)) == syd(2026, 9, 7, 5, 0)


def test_nothing_is_due_before_the_scheduled_time(db):
    """Sunday evening: last Monday is long past the grace window, and this
    Monday has not arrived."""
    assert due_occurrence(Schedule(), syd(2026, 9, 6, 20, 0)) is None


def test_a_run_missed_by_days_is_abandoned_not_run_late(db):
    """Catch-up must not mean "run Monday's scrape on Friday". The digest is
    labelled with the week it covers, so a very late run publishes a window that
    does not match its own title."""
    assert due_occurrence(Schedule(), syd(2026, 9, 11, 9, 0)) is None


def test_the_grace_window_is_the_boundary(db):
    inside = syd(2026, 9, 7, 5, 0) + timedelta(hours=11, minutes=59)
    outside = syd(2026, 9, 7, 5, 0) + timedelta(hours=12, minutes=1)
    assert due_occurrence(Schedule(), inside) is not None
    assert due_occurrence(Schedule(), outside) is None


def test_a_paused_schedule_is_never_due(db):
    assert due_occurrence(Schedule(enabled=False), syd(2026, 9, 7, 5, 30)) is None


def test_the_occurrence_returned_is_the_scheduled_instant_not_now(db):
    """It is written to `pipeline_runs.due_at`, which is what stops the same
    Monday running twice. Returning the current time instead would make every
    tick look like a different occurrence."""
    due = due_occurrence(Schedule(), syd(2026, 9, 7, 8, 30))
    assert due == syd(2026, 9, 7, 5, 0)
    assert previous_due(Schedule(), syd(2026, 9, 7, 8, 30)) == due


# ---------- validation ----------


@pytest.mark.parametrize("kwargs", [
    {"day_of_week": 7, "hour": 5, "minute": 0, "tz_name": "Australia/Sydney"},
    {"day_of_week": -1, "hour": 5, "minute": 0, "tz_name": "Australia/Sydney"},
    {"day_of_week": 0, "hour": 24, "minute": 0, "tz_name": "Australia/Sydney"},
    {"day_of_week": 0, "hour": 5, "minute": 60, "tz_name": "Australia/Sydney"},
    {"day_of_week": 0, "hour": 5, "minute": 0, "tz_name": "AEST"},
    {"day_of_week": 0, "hour": 5, "minute": 0, "tz_name": "Sydney"},
])
def test_an_invalid_schedule_is_refused_when_it_is_set(kwargs):
    """Not when it fails to fire — a schedule that silently never runs is the
    worst outcome this feature can produce."""
    with pytest.raises(ScheduleError):
        validate(**kwargs)


def test_a_valid_schedule_passes(db):
    validate(2, 23, 59, "Pacific/Port_Moresby")


# ---------- storage ----------


def test_a_changed_schedule_round_trips(db):
    set_schedule(enabled=True, day_of_week=3, hour=7, minute=30,
                 tz_name="Pacific/Port_Moresby", changed_by="boss@easyskill.com", target=db)

    sched = get_schedule(db)
    assert sched.describe() == "Thursday at 07:30 Pacific/Port_Moresby"
    assert sched.changed_by == "boss@easyskill.com"
    assert sched.changed_at


def test_changing_it_twice_updates_one_row(db):
    set_schedule(enabled=True, day_of_week=1, hour=6, minute=0,
                 tz_name="Australia/Sydney", changed_by="a", target=db)
    set_schedule(enabled=True, day_of_week=2, hour=8, minute=15,
                 tz_name="Australia/Sydney", changed_by="b", target=db)

    with connect(db, readonly=True) as conn:
        n = conn.execute("SELECT count(*) FROM schedule_settings").fetchone()[0]
    assert n == 1
    assert get_schedule(db).describe() == "Wednesday at 08:15 Australia/Sydney"


def test_pausing_is_stored(db):
    set_schedule(enabled=False, day_of_week=0, hour=5, minute=0,
                 tz_name="Australia/Sydney", changed_by="a", target=db)
    assert get_schedule(db).enabled is False


def test_an_unreadable_table_falls_back_to_the_default(db, monkeypatch):
    """A background loop must not be stopped permanently by a database hiccup."""
    import loader.schedule as mod

    def _broken(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mod, "connect", _broken)
    assert get_schedule(db).describe() == "Monday at 05:00 Australia/Sydney"


def test_an_invalid_schedule_is_never_stored(db):
    with pytest.raises(ScheduleError):
        set_schedule(enabled=True, day_of_week=0, hour=99, minute=0,
                     tz_name="Australia/Sydney", changed_by="a", target=db)
    assert get_schedule(db).hour == 5


# ---------- the run log and its lease ----------


def test_a_claimed_run_blocks_a_second_one(db):
    """A scrape spends Gemini quota against a cap of 20 calls a day and writes
    into `signals`. Two at once double-spend and interleave."""
    run_log.claim(trigger=run_log.TRIGGER_MANUAL, started_by="a", target=db)
    with pytest.raises(run_log.RunInProgress):
        run_log.claim(trigger=run_log.TRIGGER_MANUAL, started_by="b", target=db)


def test_finishing_releases_the_lease(db):
    first = run_log.claim(trigger=run_log.TRIGGER_MANUAL, started_by="a", target=db)
    run_log.finish(first, status=run_log.STATUS_OK, collected=12, target=db)

    second = run_log.claim(trigger=run_log.TRIGGER_MANUAL, started_by="b", target=db)
    assert second != first


def test_a_dead_run_stops_blocking_once_its_lease_goes_stale(db):
    """A container killed mid-scrape leaves a `running` row nobody will ever
    close. Without an expiry that row stops the pipeline for good."""
    run_id = run_log.claim(trigger=run_log.TRIGGER_MANUAL, started_by="a", target=db)
    stale = (datetime.now(timezone.utc)
             - timedelta(minutes=run_log.STALE_LEASE_MINUTES + 5)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute("UPDATE pipeline_runs SET heartbeat_at = ? WHERE id = ?", (stale, run_id))

    assert run_log.active_run(db) is None
    run_log.claim(trigger=run_log.TRIGGER_MANUAL, started_by="b", target=db)


def test_a_heartbeat_keeps_a_slow_run_protected(db):
    """Reclaiming from a run that is merely slow would start the second scrape
    the lease exists to prevent."""
    run_id = run_log.claim(trigger=run_log.TRIGGER_MANUAL, started_by="a", target=db)
    old = (datetime.now(timezone.utc)
           - timedelta(minutes=run_log.STALE_LEASE_MINUTES + 5)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute("UPDATE pipeline_runs SET heartbeat_at = ? WHERE id = ?", (old, run_id))

    run_log.heartbeat(run_id, db)
    assert run_log.active_run(db) is not None


def test_an_occurrence_only_runs_once(db):
    due = syd(2026, 9, 7, 5, 0)
    run_id = run_log.claim(trigger=run_log.TRIGGER_SCHEDULE, due_at=due, target=db)
    run_log.finish(run_id, status=run_log.STATUS_OK, target=db)

    assert run_log.has_run_for(due, db)
    with pytest.raises(run_log.AlreadyRan):
        run_log.claim(trigger=run_log.TRIGGER_SCHEDULE, due_at=due, target=db)


def test_a_failed_occurrence_is_not_retried_all_week(db):
    """A failure that repeats every minute until Friday is worse than a missed
    week: it burns quota and fills the history. The failure is visible either
    way, which is what the history is for."""
    due = syd(2026, 9, 7, 5, 0)
    run_id = run_log.claim(trigger=run_log.TRIGGER_SCHEDULE, due_at=due, target=db)
    run_log.finish(run_id, status=run_log.STATUS_FAILED, note="Gemini 503", target=db)

    assert run_log.has_run_for(due, db)


def test_the_next_occurrence_is_a_different_run(db):
    for due in (syd(2026, 9, 7, 5, 0), syd(2026, 9, 14, 5, 0)):
        run_id = run_log.claim(trigger=run_log.TRIGGER_SCHEDULE, due_at=due, target=db)
        run_log.finish(run_id, status=run_log.STATUS_OK, target=db)

    assert len(run_log.recent(10, db)) == 2


def test_manual_runs_do_not_collide_with_each_other(db):
    """They all carry a NULL `due_at`, and the unique index constrains scheduled
    occurrences only — a second "Run now" is an administrator being deliberate,
    a second automatic Monday is a bug."""
    for _ in range(3):
        run_id = run_log.claim(trigger=run_log.TRIGGER_MANUAL, started_by="a", target=db)
        run_log.finish(run_id, status=run_log.STATUS_OK, target=db)

    assert len(run_log.recent(10, db)) == 3


def test_an_unreadable_history_reports_the_occurrence_as_run(db, monkeypatch):
    """The one place in this feature that fails closed. If we cannot tell
    whether Monday already ran, not running costs a late digest; a wrong "no"
    costs an unbounded loop of scrapes."""
    import loader.run_log as mod

    def _broken(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mod, "connect", _broken)
    assert run_log.has_run_for(syd(2026, 9, 7, 5, 0), db) is True


def test_the_history_records_what_happened(db):
    run_id = run_log.claim(trigger=run_log.TRIGGER_MANUAL, started_by="boss@easyskill.com",
                           target=db)
    run_log.finish(run_id, status=run_log.STATUS_OK, collected=146, target=db)

    entry = run_log.recent(1, db)[0]
    assert entry["trigger"] == "manual"
    assert entry["startedBy"] == "boss@easyskill.com"
    assert entry["collected"] == 146
    assert entry["finishedAt"]


# ---------- the ticker ----------


def _tick_with(monkeypatch, db, *, now, ran):
    """Run one scheduler tick against `db` at a chosen moment."""
    import api.scheduler as sched_mod

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(sched_mod, "datetime", _Clock)
    monkeypatch.setattr(sched_mod, "get_schedule", lambda *a, **k: get_schedule(db))
    # Bound before patching: the tick calls `run_log.has_run_for` with no
    # target, and this points it at the test database.
    real_has_run_for = run_log.has_run_for
    monkeypatch.setattr(run_log, "has_run_for",
                        lambda due, target=None: real_has_run_for(due, db))

    async def _fake_pipeline(**kwargs):
        ran.append(kwargs)
        return {"runId": "x", "scraped": 0}

    monkeypatch.setattr(sched_mod, "run_pipeline", _fake_pipeline)
    asyncio.run(sched_mod._tick())


def test_the_ticker_starts_a_run_when_one_is_due(db, monkeypatch):
    ran: list[dict] = []
    _tick_with(monkeypatch, db, now=syd(2026, 9, 7, 5, 2), ran=ran)

    assert len(ran) == 1
    assert ran[0]["due_at"] == syd(2026, 9, 7, 5, 0)
    assert ran[0]["trigger"] == "schedule"


def test_the_ticker_does_nothing_when_nothing_is_due(db, monkeypatch):
    ran: list[dict] = []
    _tick_with(monkeypatch, db, now=syd(2026, 9, 3, 14, 0), ran=ran)
    assert ran == []


def test_the_ticker_does_not_rerun_an_occurrence_after_a_restart(db, monkeypatch):
    """The restart case, end to end: the run happened, the container came back
    at 05:30, and the tick must not scrape a second time."""
    due = syd(2026, 9, 7, 5, 0)
    run_id = run_log.claim(trigger=run_log.TRIGGER_SCHEDULE, due_at=due, target=db)
    run_log.finish(run_id, status=run_log.STATUS_OK, target=db)

    ran: list[dict] = []
    _tick_with(monkeypatch, db, now=syd(2026, 9, 7, 5, 30), ran=ran)
    assert ran == []


def test_the_ticker_does_not_run_a_paused_schedule(db, monkeypatch):
    set_schedule(enabled=False, day_of_week=0, hour=5, minute=0,
                 tz_name="Australia/Sydney", changed_by="a", target=db)

    ran: list[dict] = []
    _tick_with(monkeypatch, db, now=syd(2026, 9, 7, 5, 2), ran=ran)
    assert ran == []


def test_a_failing_run_does_not_kill_the_loop(db, monkeypatch):
    """An exception escaping the tick would cancel the task and stop the
    pipeline for good, with nothing but a silent absence to show for it."""
    import api.scheduler as sched_mod

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return syd(2026, 9, 7, 5, 2)

    monkeypatch.setattr(sched_mod, "datetime", _Clock)
    monkeypatch.setattr(sched_mod, "get_schedule", lambda *a, **k: get_schedule(db))
    monkeypatch.setattr(run_log, "has_run_for", lambda due, target=None: False)

    async def _boom(**_kwargs):
        raise RuntimeError("the scraper fell over")

    monkeypatch.setattr(sched_mod, "run_pipeline", _boom)

    asyncio.run(sched_mod._tick())  # must not raise


# ---------- where the clock comes from ----------
#
# The schedule means "05:00 in Sydney". The server it runs on may be anywhere,
# and may move. These pin that the machine's own timezone is never consulted.


@pytest.mark.parametrize("server_tz", ["UTC", "America/New_York", "Europe/Berlin",
                                       "Asia/Kathmandu", "Australia/Sydney"])
def test_the_servers_own_timezone_does_not_move_the_run(server_tz, monkeypatch):
    """A VPS in Frankfurt and one in Sydney must fire at the same instant. The
    schedule is anchored to its stored IANA zone; the host clock is only ever
    read as UTC, which has no local time to be wrong about."""
    monkeypatch.setenv("TZ", server_tz)
    if hasattr(time, "tzset"):
        time.tzset()

    when = next_due(Schedule(), datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc))
    assert when == syd(2026, 9, 7, 5, 0)
    assert when.astimezone(SYD).hour == 5


def test_every_clock_read_in_the_scheduler_is_utc():
    """A single naive `datetime.now()` anywhere in this path would make the run
    time depend on where the container happens to be deployed — and it would
    look correct in development, where the developer is in Sydney anyway."""
    import ast
    import pathlib

    for name in ("loader/schedule.py", "loader/run_log.py", "api/scheduler.py"):
        tree = ast.parse(pathlib.Path(name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in {"now", "utcnow", "today"}:
                assert fn.attr == "now", f"{name}: {fn.attr}() has no timezone"
                assert node.args, f"{name}: datetime.now() with no timezone argument"


def test_a_timestamp_is_stored_as_utc_whatever_zone_it_arrives_in():
    """`due_at` is unique and compared as *text*. The same instant written
    `2026-09-07T05:00:00+10:00` and `2026-09-06T19:00:00+00:00` would read as
    two different occurrences, and Monday would run twice."""
    from loader.run_log import _stamp

    moment = datetime(2026, 9, 7, 5, 0, tzinfo=SYD)
    assert _stamp(moment) == _stamp(moment.astimezone(timezone.utc))
    assert _stamp(moment).endswith("+00:00")


def test_a_naive_timestamp_is_refused_rather_than_guessed():
    """Assuming UTC for a datetime that carries no zone would silently shift the
    schedule by the size of the guess."""
    from loader.run_log import _stamp

    with pytest.raises(ValueError, match="timezone"):
        _stamp(datetime(2026, 9, 7, 5, 0))


def test_the_same_occurrence_is_not_claimed_twice_across_zones(db):
    """The end of that chain: claiming with a Sydney-spelled instant and then a
    UTC-spelled one must be refused as the single occurrence it is."""
    as_sydney = datetime(2026, 9, 7, 5, 0, tzinfo=SYD)
    run_id = run_log.claim(trigger=run_log.TRIGGER_SCHEDULE, due_at=as_sydney, target=db)
    run_log.finish(run_id, status=run_log.STATUS_OK, target=db)

    with pytest.raises(run_log.AlreadyRan):
        run_log.claim(trigger=run_log.TRIGGER_SCHEDULE,
                      due_at=as_sydney.astimezone(timezone.utc), target=db)
