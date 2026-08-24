"""The Admin section: who has access, and how the collection sources are doing.

Every endpoint here is behind `require_admin`, not just `require_user`. Hiding
the section in the browser is presentation; this is what stops a member reading
it by typing the URL.

Two concerns live here because they are the two things an administrator does:
decide who gets in, and check the machine is still collecting.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from api import access
from api.auth import require_admin
from config.settings import settings
from loader.db import connect
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
        with connect() as conn:
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

    out: list[dict[str, Any]] = []
    for name in SOURCE_NAMES:
        info = SOURCE_INFO.get(name, {"label": name, "market": "—", "kind": "—"})
        s = stats.get(name, {})
        configured, missing = _configured(name)
        last_seen = s.get("lastSeen")

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
        })

    return {
        "sources": out,
        "staleAfterDays": STALE_AFTER_DAYS,
        "perSourceLimit": 50,
        "totalRecords": sum(s.get("total", 0) for s in stats.values()),
    }
