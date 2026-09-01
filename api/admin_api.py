"""The Admin section: who has access, and how the collection sources are doing.

Every endpoint here is behind `require_admin`, not just `require_user`. Hiding
the section in the browser is presentation; this is what stops a member reading
it by typing the URL.

Two concerns live here because they are the two things an administrator does:
decide who gets in, and check the machine is still collecting.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from api import access, scheduler
from api.auth import require_admin
from config.settings import settings
from llm import available_providers, describe_routing
from llm.usage import budget, history
from loader import run_log
from loader.llm_settings import UnknownPurpose, clear_route, set_route
from loader.db import connect
from loader.schedule import (
    DAY_NAMES,
    DEFAULT_GRACE_HOURS,
    Schedule,
    ScheduleError,
    due_occurrence,
    get_schedule,
    next_due,
    set_schedule,
)
from loader.source_settings import (
    OFF_BY_DEFAULT_REASON,
    UnknownSource,
    default_enabled,
    list_settings,
    set_enabled,
)
from scraper import SOURCE_NAMES

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

#: A weekly pipeline that has not run in this long is not merely idle.
STALE_AFTER_DAYS = 8

#: What each source needs before it can collect anything, so the page can say
#: "not configured" rather than showing an unexplained zero.
SOURCE_INFO: dict[str, dict[str, str]] = {
    "pngworkforce": {"label": "PNGworkforce", "market": "PNG", "kind": "Job board"},
    "seek": {"label": "SEEK", "market": "AU", "kind": "Job board"},
    "adzuna": {"label": "Adzuna", "market": "AU", "kind": "JSON API"},
    "newsfeed": {"label": "Industry news", "market": "AU + PNG", "kind": "RSS"},
}


# --------------------------------------------------------------------------
# Access
# --------------------------------------------------------------------------


@router.get("/access")
def list_access(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Everyone who can sign in, from every route.

    `envGrants` is listed alongside the editable rows because an access route
    nobody can see is one nobody revokes. Those entries are marked
    `source: "environment"` — this screen cannot change them.
    """
    return {
        "users": access.list_users(),
        "envGrants": access.env_grants(),
        #: The Workspace domain admits everyone at Easy Skill as a member
        #: without appearing in either list, so it is stated separately.
        "domain": settings.allowed_google_domain or None,
        "roles": list(access.ROLES),
        "you": user["email"],
    }


@router.post("/access", status_code=201)
def grant_access(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Grant access, or change someone's role.

    The email may be at any domain — that is the point. Easy Skill staff are
    already admitted by the Workspace rule; this is for everyone else, and for
    promoting anyone to administrator.
    """
    try:
        access.upsert_user(
            str(payload.get("email", "")),
            str(payload.get("role") or access.ROLE_MEMBER),
            added_by=user["email"],
            note=(str(payload["note"]).strip() or None) if payload.get("note") else None,
        )
    except access.AccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return list_access(user)


@router.delete("/access/{email}")
def revoke_access(
    email: str,
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Remove a grant. Refuses on the last administrator.

    Note this only revokes a *database* grant. Someone admitted by the Workspace
    domain or by ALLOWED_EMAILS still gets in — the response says so rather than
    letting an admin believe the door is shut.
    """
    try:
        removed = access.remove_user(email, removed_by=user["email"])
    except access.AccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="That account is not on the list.")

    result = list_access(user)
    normalised = access.normalise(email)
    still_in = None
    if normalised in {g["email"] for g in result["envGrants"]}:
        still_in = ("They are also in ALLOWED_EMAILS, so they can still sign in. "
                    "Remove them from the environment and restart to close that route.")
    elif result["domain"] and normalised.endswith("@" + result["domain"]):
        still_in = (f"They hold a {result['domain']} account, so the Workspace rule "
                    "still admits them as a member.")
    result["warning"] = still_in
    return result


# --------------------------------------------------------------------------
# Source health
# --------------------------------------------------------------------------


def _configured(source: str) -> tuple[bool, str | None]:
    """Whether a source can currently collect, and what is missing if not."""
    if source == "adzuna" and not settings.adzuna_configured:
        return False, "ADZUNA_APP_ID / ADZUNA_APP_KEY are not set, so this source is skipped."
    return True, None


@router.get("/sources")
def source_health(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Per-source collection health, measured from the signals themselves.

    There is no separate run log: every row already carries `source_name` and
    `captured_at`, so the last run, its size and the running totals are all
    derivable. A table recording the same facts a second time could disagree
    with them.
    """
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=7)).isoformat(timespec="seconds")

    stats: dict[str, dict[str, Any]] = {}
    try:
        with connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT source_name, count(*) AS total, "
                "max(captured_at) AS last_seen, "
                "sum(CASE WHEN classified_at IS NULL THEN 1 ELSE 0 END) AS pending, "
                "count(DISTINCT substr(captured_at, 1, 10)) AS run_days "
                "FROM signals GROUP BY source_name"
            ).fetchall()
            for r in rows:
                stats[str(r["source_name"])] = {
                    "total": int(r["total"] or 0),
                    "lastSeen": r["last_seen"],
                    "pending": int(r["pending"] or 0),
                    "runDays": int(r["run_days"] or 0),
                }
            recent = conn.execute(
                "SELECT source_name, count(*) AS n FROM signals "
                "WHERE captured_at >= ? GROUP BY source_name", (since,)
            ).fetchall()
            for r in recent:
                stats.setdefault(str(r["source_name"]), {})["last7"] = int(r["n"] or 0)

            # Size of each source's most recent run, which is what tells you
            # whether the per-source limit truncated it.
            last_run = conn.execute(
                "SELECT source_name, substr(captured_at, 1, 10) AS d, count(*) AS n "
                "FROM signals GROUP BY source_name, substr(captured_at, 1, 10)"
            ).fetchall()
            newest: dict[str, tuple[str, int]] = {}
            for r in last_run:
                name, day, n = str(r["source_name"]), str(r["d"]), int(r["n"] or 0)
                if name not in newest or day > newest[name][0]:
                    newest[name] = (day, n)
            for name, (_day, n) in newest.items():
                stats.setdefault(name, {})["lastRunRecords"] = n
    except Exception as exc:  # noqa: BLE001 - an unreachable database is a status, not a crash
        log.warning("admin: could not read source health (%s)", exc)

    settings_by_source = list_settings()

    out: list[dict[str, Any]] = []
    for name in SOURCE_NAMES:
        info = SOURCE_INFO.get(name, {"label": name, "market": "—", "kind": "—"})
        s = stats.get(name, {})
        configured, missing = _configured(name)
        last_seen = s.get("lastSeen")
        chosen = settings_by_source.get(name, {"enabled": True})

        if not configured:
            status = "not_configured"
        elif not last_seen:
            status = "never_run"
        else:
            try:
                age = (now - datetime.fromisoformat(str(last_seen))).days
            except ValueError:
                age = 999
            status = "ok" if age <= STALE_AFTER_DAYS else "stale"

        out.append({
            "name": name,
            "label": info["label"],
            "market": info["market"],
            "kind": info["kind"],
            "status": status,
            "note": missing,
            "lastSeen": last_seen,
            "totalRecords": s.get("total", 0),
            "last7Days": s.get("last7", 0),
            "lastRunRecords": s.get("lastRunRecords", 0),
            "pending": s.get("pending", 0),
            "runDays": s.get("runDays", 0),
            #: Whether the next scrape will use it. Distinct from `status`,
            #: which describes what it has been doing — a source can be
            #: collecting healthily and still be switched off for the next run.
            "enabled": bool(chosen.get("enabled", True)),
            "changedBy": chosen.get("changedBy"),
            "changedAt": chosen.get("changedAt"),
            #: Whether this source ships off, and why. The panel shows the
            #: reason beside the toggle: a source that is off for a good reason
            #: looks identical to one somebody switched off by accident, and
            #: the difference is the whole point.
            "defaultEnabled": chosen.get("defaultEnabled", True),
            "offReason": chosen.get("offReason"),
        })

    # Sources that have rows but are no longer registered — a renamed or removed
    # scraper. Worth surfacing rather than silently dropping their history.
    for name in sorted(set(stats) - set(SOURCE_NAMES)):
        s = stats[name]
        out.append({
            "name": name, "label": name, "market": "—", "kind": "Retired",
            "status": "retired",
            "note": "This source is no longer registered, but its signals remain.",
            "lastSeen": s.get("lastSeen"), "totalRecords": s.get("total", 0),
            "last7Days": s.get("last7", 0), "lastRunRecords": s.get("lastRunRecords", 0),
            "pending": s.get("pending", 0), "runDays": s.get("runDays", 0),
            # A retired source is not selectable; it has no scraper to run.
            "enabled": False, "changedBy": None, "changedAt": None,
            "defaultEnabled": False, "offReason": None,
        })

    return {
        "sources": out,
        "staleAfterDays": STALE_AFTER_DAYS,
        "perSourceLimit": 50,
        "totalRecords": sum(s.get("total", 0) for s in stats.values()),
        #: How many sources the next scrape will actually use. Zero is allowed
        #: — pausing collection is a legitimate thing to do — but the UI has to
        #: say so loudly, or an empty week looks like a broken pipeline.
        "enabledCount": sum(1 for v in settings_by_source.values() if v["enabled"]),
    }


@router.patch("/sources/{source_name}")
def set_source_enabled(
    source_name: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Turn a source on or off for the next scrape.

    Takes effect on the next pipeline run; it does not touch anything already
    collected. Turning everything off is permitted — that is how you pause
    collection — and `enabledCount` in the listing is what makes it visible.
    """
    try:
        set_enabled(
            source_name,
            bool(payload.get("enabled", True)),
            changed_by=user["email"],
            note=(str(payload["note"]).strip() or None) if payload.get("note") else None,
        )
    except UnknownSource as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result = source_health(user)
    # Switching on a source that ships off is allowed — the reason may no longer
    # hold, and an administrator is entitled to decide that. But it is not done
    # silently: without this the panel would show SEEK on, collect nothing all
    # week, and give nobody a way to connect the two.
    turned_on = bool(payload.get("enabled", True))
    if turned_on and not default_enabled(source_name):
        result["warning"] = OFF_BY_DEFAULT_REASON.get(
            source_name,
            f"{source_name} is switched off by default. Turning it on may not collect anything.",
        )
    return result


# --------------------------------------------------------------------------
# The scheduled run
# --------------------------------------------------------------------------


def _schedule_payload(sched: Schedule) -> dict[str, Any]:
    upcoming = next_due(sched, datetime.now(timezone.utc))
    return {
        "enabled": sched.enabled,
        "dayOfWeek": sched.day_of_week,
        "hour": sched.hour,
        "minute": sched.minute,
        "timezone": sched.timezone,
        "changedBy": sched.changed_by,
        "changedAt": sched.changed_at,
        "describe": sched.describe(),
        #: Computed here rather than in the browser: the answer depends on the
        #: IANA zone and the daylight-saving rules for it, and the browser's
        #: idea of "Monday 05:00 in Sydney" is its own timezone's.
        "nextRunAt": upcoming.isoformat() if upcoming else None,
        "dayNames": list(DAY_NAMES),
        "graceHours": DEFAULT_GRACE_HOURS,
        #: Whether any process is actually watching this schedule. A time set on
        #: a server with SCHEDULER_ENABLED unset would sit there looking correct
        #: and never fire, which is the worst possible failure for this feature.
        "schedulerRunning": settings.scheduler_enabled,
        "activeRun": run_log.active_run(),
        "history": run_log.recent(8),
    }


@router.get("/schedule")
def read_schedule(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """When the pipeline runs by itself, and how the recent runs went."""
    return _schedule_payload(get_schedule())


@router.put("/schedule")
def write_schedule(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Change the day, time or timezone of the automatic run.

    Takes effect within a minute — the ticker re-reads this on every pass rather
    than caching it at startup, so a change does not wait for a redeploy.
    """
    try:
        sched = set_schedule(
            enabled=bool(payload.get("enabled", True)),
            day_of_week=int(payload.get("dayOfWeek", 0)),
            hour=int(payload.get("hour", 5)),
            minute=int(payload.get("minute", 0)),
            tz_name=str(payload.get("timezone") or "Australia/Sydney"),
            changed_by=user["email"],
        )
    except ScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Day, hour and minute must be numbers.") from exc
    return _schedule_payload(sched)


@router.post("/schedule/run", status_code=202)
async def run_now(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Start a pipeline run immediately, without waiting for the schedule.

    Deliberately does not wait for it to finish: a full cycle is minutes of
    scraping and Gemini calls, far longer than any sensible HTTP timeout. The
    response says it started; the history says how it went.

    This goes through the same lease as the scheduled run, so pressing it during
    the Monday run is refused rather than starting a second scrape.

    It also *satisfies* a scheduled run that is still owed. If the server was
    down at 05:00 and an administrator presses this at 08:00, the occurrence is
    still inside its catch-up window and the ticker would otherwise start a
    second scrape a minute later — collecting the same week twice and spending
    the Gemini quota twice. Recording which occurrence this run served is what
    prevents that; the trigger stays "manual", because that is who started it.
    """
    def _claim() -> str:
        due = due_occurrence(get_schedule(), datetime.now(timezone.utc))
        if due is not None and run_log.has_run_for(due):
            # Already served, so this is an extra run somebody deliberately
            # asked for rather than the week's scheduled one.
            due = None
        try:
            return run_log.claim(trigger=run_log.TRIGGER_MANUAL, due_at=due,
                                 started_by=user["email"])
        except run_log.AlreadyRan:
            # Another process claimed the occurrence between the check and the
            # insert. The administrator still asked for a run, so give them one
            # that is not tied to an occurrence.
            return run_log.claim(trigger=run_log.TRIGGER_MANUAL,
                                 started_by=user["email"])

    try:
        # Claim synchronously so a refusal is a 409 the administrator sees,
        # rather than a failure that only appears in the log a second later.
        run_id = await asyncio.to_thread(_claim)
    except run_log.RunInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    asyncio.create_task(_finish_manual_run(run_id))
    return {"started": True, "runId": run_id,
            "note": "The run has started. It takes a few minutes; this page shows how it went."}


async def _finish_manual_run(run_id: str) -> None:
    """Run an already-claimed manual run. Failures are recorded by
    `execute_run`; this only stops the exception reaching an unwatched task."""
    try:
        await scheduler.execute_run(run_id)
    except Exception:  # noqa: BLE001 - already recorded against the run
        log.warning("admin: manual run %s ended in failure", run_id)


# --------------------------------------------------------------------------
# Which model answers which question
# --------------------------------------------------------------------------


@router.get("/llm")
def llm_settings(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Model routing, what each provider offers, and what has been spent today.

    Usage sits beside the choice deliberately. Picking a stronger model is a
    decision about cost, and on the free tier it is a decision about whether the
    weekly run will complete at all — the pipeline has already exhausted a day's
    allowance mid-cycle. Showing the two apart would let somebody make the first
    decision without seeing the second.
    """
    return {
        "routing": describe_routing(),
        "providers": available_providers(),
        "usage": budget(),
        "history": history(14),
        "you": user["email"],
    }


@router.put("/llm/{purpose}")
def set_llm_route(
    purpose: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Point one purpose at a provider and model.

    Takes effect on the next call; nothing is cached across requests. Routing to
    a provider with no key is allowed and reported rather than refused — a
    deployment may be about to gain one, and refusing here would mean the
    setting could not be made until the key existed.
    """
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    if not provider or not model:
        raise HTTPException(status_code=400, detail="Both a provider and a model are required.")

    known = {p["name"] for p in available_providers()}
    if provider not in known:
        raise HTTPException(status_code=400,
                            detail=f"'{provider}' is not a provider. Known: {', '.join(sorted(known))}.")
    try:
        set_route(purpose, provider, model, changed_by=user["email"])
    except UnknownPurpose as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result = llm_settings(user)
    chosen = next((p for p in result["providers"] if p["name"] == provider), None)
    if chosen and not chosen["configured"]:
        result["warning"] = (
            f"{chosen['label']} has no API key configured, so this purpose will fall back "
            f"to reporting an error until one is set. The choice has been saved."
        )
    return result


@router.delete("/llm/{purpose}")
def clear_llm_route(
    purpose: str,
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Return a purpose to the environment setting, or the built-in default."""
    clear_route(purpose)
    log.info("admin: %s reset the model for %s", user["email"], purpose)
    return llm_settings(user)
