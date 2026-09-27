"""Finish a run that left records unclassified — the admin's "Retry" button.

A run can end with part of its collection unread: the AI provider failed even
after the automatic retries (see `llm.retry`), or the day's AI calls ran out.
Those records are kept, and the next run classifies them — but that is a week
away, and until then they are missing from the digest, the feed and the
dashboard, and the run reads "Incomplete".

This does, on request, what was twice done by hand from a script:

1. classify what is waiting;
2. rebuild that run's Market Pulse and archived digest, so they include it;
3. update the run's note — cleared when nothing is left, otherwise saying why.

It does not scrape, and it does not post to Slack: the digest already went out
when the run finished, and posting a second copy of the week unprompted would
be noise. It refuses while a pipeline run is in flight, which classifies the
same records itself.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from agents.signal_analyst import classify_pending
from api.digest_service import build_digest_payload
from delivery.digest import build_digest
from delivery.pulse import generate_pulse, load_pulse, save_pulse
from loader import run_log
from loader.db import resolve_target
from loader.digest_archive import save_digest
from pipeline.live import _count_for_run, _unclassified_note

log = logging.getLogger(__name__)

#: One retry at a time in this process. Two at once would send the same
#: records to the AI twice.
_lock = threading.Lock()
_state: dict[str, Any] = {}


class RetryRefused(RuntimeError):
    """Why the retry cannot start. The message reaches the administrator."""


def in_progress() -> dict[str, Any] | None:
    """The retry running now — run id, who and when — or None."""
    return dict(_state) if _state else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check(run_id: str, target=None) -> dict[str, Any]:
    """Refuse early, with a reason, so the button can say why before it starts."""
    run = run_log.get(run_id, target)
    if run is None:
        raise RetryRefused("There is no such run.")
    if run["status"] == run_log.STATUS_RUNNING:
        raise RetryRefused("That run is still in progress.")
    if run_log.active_run(target):
        raise RetryRefused("A pipeline run is in progress — it classifies waiting records "
                           "itself. Try again when it has finished.")
    if _state:
        raise RetryRefused("A retry is already running.")
    return run


def retry_unclassified(run_id: str, *, by: str, target: str | Path | None = None,
                       gemini_caller: Callable[..., Any] | None = None,
                       pulse_caller: Callable[..., Any] | None = None,
                       do_pulse: bool = True) -> dict[str, Any]:
    """Classify what `run_id` left waiting, then rebuild its pulse and digest.

    The two callers are for tests; left None, each uses the model an
    administrator routed that job to.
    """
    target = resolve_target(target)
    check(run_id, target)
    if not _lock.acquire(blocking=False):
        raise RetryRefused("A retry is already running.")
    _state.update({"runId": run_id, "startedBy": by, "startedAt": _now()})
    try:
        return _retry(run_id, by=by, target=target, gemini_caller=gemini_caller,
                      pulse_caller=pulse_caller, do_pulse=do_pulse)
    finally:
        _state.clear()
        _lock.release()


def _retry(run_id: str, *, by: str, target, gemini_caller, pulse_caller,
           do_pulse: bool) -> dict[str, Any]:
    waiting = _count_for_run(target, run_id, classified=False)
    log.info("retry: %s retrying run %s (%d waiting)", by, run_id, waiting)
    counts: dict[str, Any] = {}
    failure = None
    if waiting:
        # Everything waiting, oldest first, within the day's AI calls — the same
        # call a run makes, so the same rules and limits apply.
        try:
            counts = classify_pending(target, gemini_caller=gemini_caller)
        except Exception as exc:  # noqa: BLE001 - reported on the run, not swallowed
            # No model configured, a key that was revoked: the classifier could
            # not start at all. The button ran in the background, so the run's
            # note is the only place the administrator will see why.
            log.warning("retry: classification could not start (%s)", exc)
            failure = str(exc)[:200]

    left = _count_for_run(target, run_id, classified=False)
    classified = _count_for_run(target, run_id, classified=True)

    pulse_status = None
    if classified and waiting != left:
        # Something new was classified, so the week's read and the archived
        # digest are out of date. Rebuilt exactly as the run built them.
        payload = build_digest_payload(target, days=7, run_id=run_id)
        since = datetime.now(timezone.utc) - timedelta(days=7)
        w_from = payload.get("collectedFrom") or since.isoformat(timespec="seconds")
        w_to = payload.get("collectedTo") or _now()
        pulse = None
        if do_pulse:
            outcome = generate_pulse(payload, target=target, gemini_caller=pulse_caller)
            save_pulse(outcome, window_from=w_from, window_to=w_to, target=target)
            pulse_status = outcome.status
            if outcome.ok:
                pulse = outcome.bullets
                payload["marketPulse"] = load_pulse(w_from, w_to, target)
        text = build_digest(target, since=since, pulse=pulse, run_id=run_id)
        save_digest(run_id=run_id, payload=payload, window_from=w_from, window_to=w_to,
                    digest_text=text, target=target)

    note = _unclassified_note(left, int(counts.get("errors") or 0),
                              bool(counts.get("quota_exhausted")))
    if failure:
        note = (f"{left} collected rows are still unclassified. Retried by {by}, but the "
                f"classifier could not start: {failure}")
    elif note:
        note = f"{note} Retried by {by} — {waiting - left} of {waiting} classified."
    run_log.set_note(run_id, note, target)
    log.info("retry: run %s — %d of %d classified, %d left", run_id, waiting - left, waiting, left)
    return {"runId": run_id, "waiting": waiting, "classified": waiting - left, "left": left,
            "pulse": pulse_status, "note": note, "failure": failure}


def waiting_for(run_id: str, target=None) -> int:
    """Records `run_id` collected that are still unclassified."""
    return _count_for_run(resolve_target(target), run_id, classified=False)


__all__ = ["RetryRefused", "check", "in_progress", "retry_unclassified", "waiting_for"]
