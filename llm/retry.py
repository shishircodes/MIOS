"""Retrying an AI call that failed on the provider's side.

A model provider has short outages: Gemini answered `ServerError` to one call in
the runs of 21 Sep and 27 Sep 2026, and in both the call just before and just
after it succeeded. Nothing retried it, so each hiccup cost a whole batch of 25
records — left unclassified until the next week's run — and made the run read
"incomplete".

Two kinds of failure are retried, differently:

* **Server errors** (5xx, "overloaded", "unavailable", a dropped connection) are
  the provider's problem and usually clear in seconds, so they are retried after
  a short, growing wait.
* **Rate limits** (429, "quota", "resource exhausted") need the per-minute
  window to pass, so they wait a full minute — and only where the caller asks,
  because a *daily* quota will not come back however long a run waits.

Anything else — a bad request, an answer that will not parse — is the same on
every attempt, and is raised at once.

Each attempt is a real call and is counted as one: the provider charges a
failed call against the allowance the same as a served one.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

#: Seconds to wait before each retry of a server error. Two short ones for a
#: hiccup, then a minute: on 5 Oct 2026 Gemini answered `ServerError` for about
#: ninety seconds, the two short retries were over in forty, and the last batch
#: and the Market Pulse both went unwritten. A provider that is properly down
#: still holds a call for under a minute and a half before it is given up on.
SERVER_RETRY_WAITS: tuple[float, ...] = (5.0, 20.0, 60.0)

#: The wait before retrying a rate limit — the per-minute window.
RATE_LIMIT_WAIT = 60.0

#: Swapped out by tests so the suite does not sleep.
_sleep: Callable[[float], None] = time.sleep

_SERVER_CLASSES = frozenset({
    "ServerError",              # google-genai, any 5xx
    "InternalServerError",      # anthropic 500
    "ServiceUnavailableError",
    "OverloadedError",          # anthropic 529
    "APIConnectionError",       # anthropic, connection dropped
    "APITimeoutError",
    "ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout",
})
_SERVER_WORDS = ("503", "502", "504", "529", "500 internal", "internal error",
                 "unavailable", "overloaded", "deadline exceeded", "try again later")
_RATE_WORDS = ("429", "rate", "quota", "resourceexhausted", "resource_exhausted",
               "resource exhausted")


def is_rate_limit(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(w in msg for w in _RATE_WORDS)


def is_server_error(exc: BaseException) -> bool:
    """Whether a failure was the provider's, and so worth trying again."""
    if is_rate_limit(exc):
        return False
    if type(exc).__name__ in _SERVER_CLASSES:
        return True
    for attr in ("code", "status_code"):
        code = getattr(exc, attr, None)
        if isinstance(code, int) and 500 <= code < 600:
            return True
    msg = str(exc).lower()
    return any(w in msg for w in _SERVER_WORDS)


def with_retry(fn: Callable[..., T], *args: Any, what: str = "AI call",
               rate_limit_retries: int = 0,
               server_waits: tuple[float, ...] = SERVER_RETRY_WAITS,
               **kwargs: Any) -> T:
    """Call `fn(*args, **kwargs)`, retrying provider-side failures."""
    server_tries = 0
    rate_tries = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - classified below, re-raised otherwise
            if is_rate_limit(exc) and rate_tries < rate_limit_retries:
                rate_tries += 1
                log.warning("%s rate-limited (retry %d of %d) — waiting %.0fs for the window "
                            "to reset", what, rate_tries, rate_limit_retries, RATE_LIMIT_WAIT)
                _sleep(RATE_LIMIT_WAIT)
                continue
            if is_server_error(exc) and server_tries < len(server_waits):
                wait = server_waits[server_tries]
                server_tries += 1
                log.warning("%s failed on the provider's side (%s: %s) — retry %d of %d in %.0fs",
                            what, type(exc).__name__, str(exc)[:120], server_tries,
                            len(server_waits), wait)
                _sleep(wait)
                continue
            if server_tries:
                log.error("%s still failing after %d retries: %s", what, server_tries, exc)
            raise
