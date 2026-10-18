"""A run whose database connection drops tries again, and nothing else does.

On 19 Oct 2026 the scheduled run failed fifteen seconds in with "consuming
input failed: SSL connection has been closed unexpectedly", collected nothing,
and stayed failed until somebody noticed. A dropped connection is the
database's side of a wake-up or a network blip; a fresh one a minute later
works.
"""
from __future__ import annotations

import asyncio

import pytest

from api import scheduler
from loader.db import is_connection_lost


class OperationalError(Exception):
    """Named like psycopg's, which is what the check looks at."""


DROPPED = OperationalError("consuming input failed: SSL connection has been closed unexpectedly")


@pytest.mark.parametrize("exc,lost", [
    (DROPPED, True),
    (OperationalError("server closed the connection unexpectedly"), True),
    (OperationalError("terminating connection due to administrator command"), True),
    (OperationalError('relation "signals" does not exist'), False),
    (ValueError("SSL connection has been closed"), False),
    (RuntimeError("Gemini quota exhausted"), False),
])
def test_only_a_lost_connection_counts(exc, lost):
    assert is_connection_lost(exc) is lost


@pytest.fixture
def no_waits(monkeypatch):
    waited = []

    async def fake_sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(scheduler.asyncio, "sleep", fake_sleep)
    return waited


def flaky(*outcomes):
    """A cycle that raises or returns each outcome in turn."""
    calls = {"n": 0}

    def cycle():
        out = outcomes[calls["n"]]
        calls["n"] += 1
        if isinstance(out, BaseException):
            raise out
        return out

    cycle.calls = calls
    return cycle


def test_a_dropped_connection_is_tried_again(no_waits):
    cycle = flaky(DROPPED, {"scraped": 393})
    assert asyncio.run(scheduler._with_connection_retry("run-1", cycle)) == {"scraped": 393}
    assert cycle.calls["n"] == 2 and no_waits == [scheduler.CONNECTION_RETRY_WAITS[0]]


def test_it_gives_up_after_its_retries(no_waits):
    cycle = flaky(*[DROPPED] * (len(scheduler.CONNECTION_RETRY_WAITS) + 1))
    with pytest.raises(OperationalError):
        asyncio.run(scheduler._with_connection_retry("run-1", cycle))
    assert no_waits == list(scheduler.CONNECTION_RETRY_WAITS)


def test_any_other_failure_is_reported_at_once(no_waits):
    cycle = flaky(RuntimeError("a real bug"))
    with pytest.raises(RuntimeError):
        asyncio.run(scheduler._with_connection_retry("run-1", cycle))
    assert cycle.calls["n"] == 1 and no_waits == []


def test_the_retries_together_wait_a_few_minutes_at_most():
    assert 60 <= sum(scheduler.CONNECTION_RETRY_WAITS) <= 300
