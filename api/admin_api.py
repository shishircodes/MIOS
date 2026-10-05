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
from llm import available_providers, describe_routing, verify
from llm.usage import budget, history
from loader import run_log
from loader.credentials import (
    CredentialError,
    CredentialsLocked,
    clear_key,
    set_key,
)
from loader.feature_settings import PUSH_RATIONALE
from loader.feature_settings import describe as describe_feature
from loader.feature_settings import set_enabled as set_feature_enabled
from loader.llm_settings import UnknownPurpose, clear_route, set_route
from loader import pipeline_settings
from pipeline.retry import RetryRefused
from pipeline.retry import check as retry_check
from pipeline.retry import in_progress as retry_in_progress
from pipeline.retry import retry_unclassified
from pipeline.retry import waiting_for as retry_waiting_for
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
    configured as source_configured,
    default_enabled,
    list_settings,
    set_enabled,
)
from scraper import SOURCE_NAMES, catalog
from scraper.publications import source_id_for

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

#: A weekly pipeline that has not run in this long is not merely idle.
STALE_AFTER_DAYS = 8

def _describe(src: catalog.Source) -> dict[str, Any]:
    """The catalogue's facts about a source, as the page shows them."""
    return {
        "name": src.id,
        "label": src.label,
        "category": src.category,
        "group": src.group,
        "market": src.market,
        "sectors": src.sectors,
        "provides": src.provides,
        #: How it is read. This was "kind" before the catalogue existed and the
        #: page still reads it under that name.
        "kind": src.access,
        "cost": src.cost,
        "priority": src.priority,
        "url": src.url,
        "collectable": src.collectable,
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


def _collection_stats() -> dict[str, dict[str, Any]]:
    """What each source has collected, keyed by catalogue id.

    Measured from the signals themselves. There is no separate run log: every
    row already carries `source_name` and `captured_at`, so the last run, its
    size and the running totals are all derivable, and a table recording the
    same facts a second time could disagree with them.

    Rows stored under the old shared name "newsfeed" are counted towards the
    publication they came from — see `scraper.publications.source_id_for`. That
    needs the article address, so those rows are read one by one; every other
    source is a single aggregate.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    stats: dict[str, dict[str, Any]] = {}
    days: dict[str, dict[str, int]] = {}

    def add(name: str, day: str, captured: str, n: int, pending: int) -> None:
        st = stats.setdefault(name, {"total": 0, "pending": 0, "last7": 0, "lastSeen": None})
        st["total"] += n
        st["pending"] += pending
        if captured >= since:
            st["last7"] += n
        if not st["lastSeen"] or captured > st["lastSeen"]:
            st["lastSeen"] = captured
        by_day = days.setdefault(name, {})
        by_day[day] = by_day.get(day, 0) + n

    try:
        with connect(readonly=True) as conn:
            # One row per source per collection day, which is enough to derive
            # every figure on the page.
            for r in conn.execute(
                "SELECT source_name, substr(captured_at, 1, 10) AS d, count(*) AS n, "
                "max(captured_at) AS last_seen, "
                "sum(CASE WHEN classified_at IS NULL THEN 1 ELSE 0 END) AS pending "
                "FROM signals WHERE source_name <> 'newsfeed' "
                "GROUP BY source_name, substr(captured_at, 1, 10)"
            ).fetchall():
                add(str(r["source_name"]), str(r["d"]), str(r["last_seen"] or ""),
                    int(r["n"] or 0), int(r["pending"] or 0))
            for r in conn.execute(
                "SELECT source_url, captured_at, classified_at FROM signals "
                "WHERE source_name = 'newsfeed'"
            ).fetchall():
                captured = str(r["captured_at"] or "")
                add(source_id_for("newsfeed", r["source_url"]), captured[:10], captured,
                    1, 0 if r["classified_at"] else 1)
    except Exception as exc:  # noqa: BLE001 - an unreachable database is a status, not a crash
        log.warning("admin: could not read source health (%s)", exc)

    for name, by_day in days.items():
        # Size of the most recent run, which is what tells you whether the
        # per-source limit truncated it.
        stats[name]["lastRunRecords"] = by_day[max(by_day)]
        stats[name]["runDays"] = len(by_day)
    return stats


def _integration_note(src: catalog.Source) -> tuple[str, str | None]:
    """Status and note for a source that is wired up under Integrations."""
    try:
        if src.id == "slack":
            from delivery import slack_config

            ok = bool(slack_config.webhook())
            return ("connected" if ok else "not_configured",
                    src.note if ok else "No Slack webhook has been added yet; see Integrations.")
        if src.id == "hubspot":
            from loader import hubspot_watchlist

            ok = bool(hubspot_watchlist.api_key())
            return ("connected" if ok else "not_configured",
                    src.note if ok else "No HubSpot key has been added yet; see Integrations.")
    except Exception as exc:  # noqa: BLE001 - a status line is not worth failing the page
        log.debug("admin: could not read %s integration state (%s)", src.id, exc)
    return "connected", src.note


@router.get("/sources")
def source_health(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Every source in the catalogue: what it is, and how collection is going.

    A collected source carries its health, measured from the signals. A source
    that is not collected carries the reason instead, so the page can answer
    "why are we not reading this?" as readily as "is this one working?".
    """
    now = datetime.now(timezone.utc)
    limits = pipeline_settings.scrape_limits()
    stats = _collection_stats()
    settings_by_source = list_settings()

    out: list[dict[str, Any]] = []
    for src in catalog.SOURCES:
        row = _describe(src)
        if not src.collectable:
            status, note = ((_integration_note(src)) if src.availability == catalog.CONNECTED
                            else (src.availability or "planned", src.note))
            out.append({
                **row,
                "status": status,
                "statusLabel": catalog.AVAILABILITY_LABEL.get(status),
                "note": note,
                "lastSeen": None, "totalRecords": 0, "last7Days": 0, "lastRunRecords": 0,
                "pending": 0, "runDays": 0,
                "enabled": False, "changedBy": None, "changedAt": None,
                "defaultEnabled": False, "offReason": None, "limit": None,
            })
            continue

        name = src.id
        s = stats.get(name, {})
        configured, missing = source_configured(name)
        if name == "newsfeed" and not configured and s.get("total"):
            # No custom feeds, but records under the old shared name whose
            # address matched no catalogued publication. Called what they are,
            # rather than "Custom RSS feeds" holding rows nobody configured.
            from scraper.publications import LEGACY_NEWSFEED_LABEL

            row["label"] = LEGACY_NEWSFEED_LABEL
            missing = ("Records from before each publication became a source of its own. "
                       "Add feeds under Source options below to use this for custom feeds.")
        last_seen = s.get("lastSeen")
        limit = limits.get(name, pipeline_settings.DEFAULT_SCRAPE_LIMIT)
        chosen = settings_by_source.get(name, {"enabled": True})

        # A switched-off source is not collecting, whatever its last run looked
        # like. `status` used to be derived from the age of the newest signal
        # alone, so SEEK — switched off, and holding records from eight days
        # ago — reported "Collecting" beside a toggle reading Off. The row
        # contradicted itself, and the chip was the half that was wrong.
        #
        # Ordered after `not_configured` deliberately: missing credentials
        # outlast the toggle and are the thing an administrator has to fix
        # before switching it on would achieve anything. The figures in the rest
        # of the row still carry what it collected while it was on.
        if not configured:
            status = "not_configured"
        elif not chosen.get("enabled", True):
            status = "off"
        elif not last_seen:
            status = "never_run"
        else:
            try:
                age = (now - datetime.fromisoformat(str(last_seen))).days
            except ValueError:
                age = 999
            status = "ok" if age <= STALE_AFTER_DAYS else "stale"

        out.append({
            **row,
            "status": status,
            "statusLabel": None,
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
            #: Records it takes per run, as set under Collection limits.
            "limit": limit,
        })

    # Sources that have rows but are no longer registered — a renamed or removed
    # scraper. Worth surfacing rather than silently dropping their history.
    for name in sorted(set(stats) - set(SOURCE_NAMES)):
        s = stats[name]
        out.append({
            "name": name, "label": name, "category": "retired", "group": "Retired",
            "market": "—", "sectors": "—", "provides": "—", "kind": "Retired",
            "cost": "—", "priority": None, "url": "", "collectable": False,
            "status": "retired", "statusLabel": "Retired",
            "note": "This source is no longer registered, but its signals remain.",
            "lastSeen": s.get("lastSeen"), "totalRecords": s.get("total", 0),
            "last7Days": s.get("last7", 0), "lastRunRecords": s.get("lastRunRecords", 0),
            "pending": s.get("pending", 0), "runDays": s.get("runDays", 0),
            # A retired source is not selectable; it has no scraper to run.
            "enabled": False, "changedBy": None, "changedAt": None,
            "defaultEnabled": False, "offReason": None, "limit": None,
        })

    categories = [{"key": key, "label": label} for key, label in catalog.CATEGORIES]
    if any(r["category"] == "retired" for r in out):
        categories.append({"key": "retired", "label": "Retired"})

    return {
        "sources": out,
        #: The guide's sections, in the guide's order, for grouping the page.
        "categories": categories,
        "staleAfterDays": STALE_AFTER_DAYS,
        #: The default. Each source's own limit is on its row.
        "perSourceLimit": pipeline_settings.DEFAULT_SCRAPE_LIMIT,
        "totalRecords": sum(s.get("total", 0) for s in stats.values()),
        #: How many sources MIOS can collect from at all, as opposed to how many
        #: the guide lists.
        "collectableCount": len(SOURCE_NAMES),
        #: How many sources the next scrape will actually use. Zero is allowed
        #: — pausing collection is a legitimate thing to do — but the UI has to
        #: say so loudly, or an empty week looks like a broken pipeline.
        "enabledCount": sum(1 for v in settings_by_source.values() if v["enabled"]),
    }


@router.get("/pipeline-settings")
def get_pipeline_settings(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Records per source, and how collected records are batched to the AI."""
    return pipeline_settings.describe()


@router.put("/pipeline-settings")
def put_pipeline_settings(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Change any of them. All or nothing; applies from the next run."""
    values = payload.get("values")
    if not isinstance(values, dict) or not values:
        raise HTTPException(status_code=400, detail="Send the settings to change as `values`.")
    try:
        changed = pipeline_settings.update(values, changed_by=user["email"])
    except pipeline_settings.SettingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**pipeline_settings.describe(), "changed": changed}


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
        #: An administrator's retry of a run's leftovers, while it runs.
        "retrying": retry_in_progress(),
        "history": [_with_waiting(r) for r in run_log.recent(8)],
    }


def _with_waiting(run: dict[str, Any]) -> dict[str, Any]:
    """A run, with how many of its records are still unclassified.

    Counted only for finished runs that carry a note — the ones that read
    "Incomplete" — which is all the Retry button needs.
    """
    waiting = None
    if run["status"] == run_log.STATUS_OK and run.get("note"):
        try:
            waiting = retry_waiting_for(run["id"])
        except Exception as exc:  # noqa: BLE001 - the button is optional, the row is not
            log.warning("admin: could not count waiting rows for %s (%s)", run["id"], exc)
    return {**run, "waiting": waiting}


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

    Takes effect at once: saving wakes the scheduler, which re-reads the schedule
    rather than waiting for its next check, so a change needs no redeploy.
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
    scheduler.notify_changed()
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

    if retry_in_progress():
        raise HTTPException(status_code=409, detail="A retry of the last run is classifying its "
                                                    "leftovers. Start a run when it has finished.")
    try:
        # Claim synchronously so a refusal is a 409 the administrator sees,
        # rather than a failure that only appears in the log a second later.
        run_id = await asyncio.to_thread(_claim)
    except run_log.RunInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    asyncio.create_task(_finish_manual_run(run_id))
    return {"started": True, "runId": run_id,
            "note": "The run has started. It takes a few minutes; this page shows how it went."}


@router.post("/schedule/runs/{run_id}/retry", status_code=202)
async def retry_run(run_id: str, user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Classify what a run left waiting, then rebuild its pulse and digest.

    For when the automatic retries were not enough — the provider was down for
    longer, or the day's AI calls ran out. Runs in the background, like Run
    now: with the pause between AI calls it takes a minute or more. Refused
    while a pipeline run or another retry is going.
    """
    try:
        await asyncio.to_thread(retry_check, run_id)
    except RetryRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    asyncio.create_task(_finish_retry(run_id, user["email"]))
    return {"started": True, "runId": run_id,
            "note": "Retrying. It takes a minute or two; this page shows how it went."}


async def _finish_retry(run_id: str, by: str) -> None:
    try:
        result = await asyncio.to_thread(retry_unclassified, run_id, by=by)
        log.info("admin: retry of %s by %s — %s", run_id, by, result)
    except RetryRefused as exc:
        log.info("admin: retry of %s not started (%s)", run_id, exc)
    except Exception:  # noqa: BLE001 - nobody is waiting on this task
        log.exception("admin: retry of %s failed", run_id)


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
        #: Whether Mode Push asks a model for its written notes at all.
        "pushRationale": describe_feature(PUSH_RATIONALE),
        "you": user["email"],
    }


@router.get("/llm/usage")
def llm_usage(
    range: str = "30d",  # noqa: A002 - the query parameter's public name
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Tokens used and estimated cost across a time frame.

    Estimated at list prices (see `llm.pricing`): a free-tier deployment is not
    billed at all, so this is what the same usage would cost on a paid plan —
    which is the question before routing a purpose to Claude.
    """
    from llm.usage import report

    return report(range)


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


@router.put("/push-rationale")
def set_push_rationale(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Switch Mode Push's AI notes on or off.

    Offered because the notes never touch a score or the order, so turning them
    off changes what a consultant reads and what the allowance is spent on, and
    nothing about who is contacted. Applies to the next search.
    """
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="Say whether the notes should be on or off.")
    set_feature_enabled(PUSH_RATIONALE, enabled, changed_by=user["email"])
    return llm_settings(user)


# --------------------------------------------------------------------------
# Provider API keys
# --------------------------------------------------------------------------


@router.put("/llm/keys/{provider}")
def set_provider_key(
    provider: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Store an API key for one provider, encrypted.

    The response never contains the key — not the one just sent, not any other.
    A write-only field is unusual enough to be worth stating: an administrator
    who needs to check *which* key is loaded gets the last four characters and
    who installed it, which identifies it to somebody already holding it and is
    useless to anybody else.
    """
    key = str(payload.get("key") or "").strip()
    known = {p["name"] for p in available_providers()}
    if provider not in known:
        raise HTTPException(
            status_code=400,
            detail=f"'{provider}' is not a provider. Known: {', '.join(sorted(known))}.")
    if not key:
        raise HTTPException(status_code=400, detail="An API key is required.")

    try:
        hint = set_key(provider, key, changed_by=user["email"])
    except CredentialsLocked as exc:
        # 503, not 400: the request was fine, the deployment is not configured
        # to accept it. The message names the variable to set.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except CredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = llm_settings(user)
    result["note"] = (f"Key ending …{hint} saved for {provider}. "
                      f"It takes effect on the next call — use Test to check it now.")
    return result


@router.delete("/llm/keys/{provider}")
def clear_provider_key(
    provider: str,
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Forget a stored key, returning the provider to its environment variable."""
    removed = clear_key(provider)
    log.info("admin: %s cleared the %s key (had one: %s)",
             user["email"], provider, removed)
    result = llm_settings(user)
    result["note"] = (
        f"Stored key for {provider} removed. "
        f"{'The environment variable is in use again.' if removed else 'There was none.'}")
    return result


@router.post("/llm/keys/{provider}/test")
def test_provider_key(
    provider: str,
    payload: dict[str, Any] = Body(default={}),
    user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Make one real call and report whether the key works.

    Stored is not the same as working: a key can be truncated by a paste,
    revoked, or belong to a project with the API switched off, and all three
    look identical in the panel until the weekly run fails at 05:00 on a
    Monday. Spending one call to find out now is the point of being able to
    enter a key here at all.

    The call is counted like any other, because the provider charges for it.
    """
    known = {p["name"] for p in available_providers()}
    if provider not in known:
        raise HTTPException(
            status_code=400,
            detail=f"'{provider}' is not a provider. Known: {', '.join(sorted(known))}.")

    model = str(payload.get("model") or "").strip()
    ok, message = verify(provider, model)
    log.info("admin: %s tested %s -> %s", user["email"], provider, "ok" if ok else "failed")

    result = llm_settings(user)
    result["test"] = {"provider": provider, "ok": ok, "message": message}
    return result
