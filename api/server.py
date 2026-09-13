"""FastAPI bridge between the MIOS Python pipeline and the TanStack web app.

Run:
    python -m uvicorn api.server:app --reload --port 8787

Every endpoint that returns intelligence data requires a signed-in Easy Skill
Google account (see api/auth.py). `/api/health` stays open so a load balancer or
uptime check can hit it, but it deliberately reports nothing about the data.

Endpoints:
    GET  /api/health   -> liveness only (public)
    GET  /api/me       -> current session (public; reports authenticated: false)
    GET  /auth/login   -> start Google Sign-In
    GET  /auth/callback-> OAuth redirect target
    POST /auth/logout  -> clear the session
    GET  /api/digest   -> the latest stored weekly digest  [AUTH REQUIRED]
    GET  /api/digests  -> the archive: every past digest, newest first
    GET  /api/digest/{run_id} -> one past digest
    GET  /api/signals  -> the full signal list, paginated  [AUTH REQUIRED]

The Admin section lives at /api/admin/* and is gated by `require_admin`, not
just `require_user` — a member gets 403 there even with a valid session.

Mode Publish adds /api/publish/* (see api/publish_api.py). It has no endpoint
that distributes externally, by design — see that module's docstring.

Mode Push adds /api/push/* (see api/push_api.py), all auth-required.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from api import access
from api.auth import check_google_client_id, require_user, router as auth_router
from api.digest_service import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_WINDOW_DAYS,
    MAX_PAGE_SIZE,
    build_digest_payload,
    build_feed_payload,
)
from api import scheduler
from api.dashboard_service import build_dashboard_payload
from api.admin_api import router as admin_router
from api.publish_api import router as publish_router
from api.push_api import router as push_router
from api.watchlist_api import router as watchlist_router
from config.settings import configure_logging, settings
from loader.db import backend_label, close_pool
from loader.digest_archive import latest_digest, list_digests, load_digest

configure_logging()
log = logging.getLogger("api.server")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Defined below; resolved at call time, not at definition time.
    _warn_on_insecure_config()
    # No-op once an admin exists. Without it a fresh database has no admin and
    # no way to create one from inside the app.
    access.ensure_bootstrap_admin()
    # The weekly pipeline. A no-op unless SCHEDULER_ENABLED is set, so only the
    # deployed server runs it — a developer with the production DSN in their
    # .env would otherwise start scraping the moment they ran the API.
    task = scheduler.start(_app.state)
    yield
    await scheduler.stop(task)
    # Returns the pooled database connections rather than leaving the far end to
    # time them out.
    close_pool()


app = FastAPI(title="MIOS API", version="0.2.0", lifespan=lifespan)

# Signed-cookie session. https_only is off for local dev over http; set
# SESSION_HTTPS_ONLY once the app is served over TLS.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    max_age=settings.session_max_age,
    same_site="lax",  # survives the top-level redirect back from Google
    https_only=settings.web_app_url.startswith("https://"),
)

# The browser sends the session cookie cross-origin (web app :3000 -> api :8787),
# so credentials must be allowed. That rules out the "*" wildcard: origins have
# to be listed explicitly.
ALLOWED_ORIGINS = sorted({
    settings.web_app_url,
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
})

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    # Listed explicitly rather than "*", so each one is here for a reason:
    # DELETE for /api/push/profiles/{id}, because those rows describe real
    # people and removing one has to be possible from the UI; PUT for
    # /api/admin/schedule, which replaces the single settings row rather than
    # patching a field of it. A method missing from this list fails as a
    # rejected preflight — visible only in the browser, never in the tests.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(push_router)
app.include_router(publish_router)
app.include_router(admin_router)
app.include_router(watchlist_router)


def _warn_on_insecure_config() -> None:
    if settings.auth_disabled:
        log.warning(
            "=" * 72 + "\n"
            "AUTH_DISABLED=true — the MIOS API is serving data with NO sign-in.\n"
            "This is a local development convenience. Never run this way.\n"
            + "=" * 72
        )
    elif not settings.oauth_configured:
        log.warning(
            "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET are not set — sign-in cannot "
            "complete, so all data endpoints will return 401. See .env.example."
        )
    else:
        for problem in check_google_client_id(settings.google_client_id):
            log.error("GOOGLE_CLIENT_ID looks wrong: %s", problem)
        if not settings.allowed_google_domain and not settings.allowed_emails:
            log.warning(
                "Neither ALLOWED_GOOGLE_DOMAIN nor ALLOWED_EMAILS is set — any Google "
                "account will be able to sign in."
            )


@app.get("/api/health")
def health() -> dict:
    """Public liveness probe. Intentionally free of any pipeline data."""
    return {"status": "ok", "authRequired": not settings.auth_disabled}


@app.get("/api/digest")
def digest(
    days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=365, description="capture window in days"),
    user: dict[str, Any] = Depends(require_user),
) -> dict:
    """The most recent stored digest.

    A digest belongs to the pipeline run that produced it and is written when
    that run finishes, so this serves a snapshot rather than recomputing one.
    That is what stops a page load blending two runs into a single "week".

    Falls back to computing over the rolling window when nothing has been
    archived yet — a fresh database, or one whose signals predate the archive.
    The response says which of the two it is, so the dashboard can tell the
    reader rather than passing a live computation off as a published digest.
    """
    stored = latest_digest()
    if stored is not None:
        stored["backend"] = backend_label()
        stored["live"] = False
        log.info("api: /api/digest served to %s (archived run %s)",
                 user["email"], stored.get("archived", {}).get("runId"))
        return stored

    log.info("api: /api/digest served to %s (no archive yet; live window=%dd)",
             user["email"], days)
    payload = build_digest_payload(days=days)
    # Name the engine, never the DSN — a Neon connection string embeds the
    # password, so it must not reach the browser or a log line.
    payload["backend"] = backend_label()
    payload["live"] = True
    payload["archived"] = None
    return payload


@app.get("/api/dashboard")
def dashboard(user: dict[str, Any] = Depends(require_user)) -> dict:
    """Hiring trends, counted from the signals themselves.

    A point per collection rather than per calendar week: the pipeline runs
    weekly so they usually coincide, but a missed run leaves a gap a calendar
    series would have to fill, and every way of filling it asserts something
    nobody measured.
    """
    payload = build_dashboard_payload()
    log.info("api: /api/dashboard served to %s (%d collections)",
             user["email"], payload["coverage"]["collections"])
    return payload


@app.get("/api/digests")
def digest_archive(
    limit: int = Query(26, ge=1, le=200),
    user: dict[str, Any] = Depends(require_user),
) -> dict:
    """Every stored digest, newest first, without payloads.

    This is a picker, not a reader: it carries the window and the signal count
    so somebody can choose which week to open, and nothing else.
    """
    entries = list_digests(limit)
    log.info("api: /api/digests served to %s (%d entries)", user["email"], len(entries))
    return {"digests": entries}


@app.get("/api/digest/{run_id}")
def digest_by_run(
    run_id: str,
    user: dict[str, Any] = Depends(require_user),
) -> dict:
    """One past digest, exactly as it was published."""
    stored = load_digest(run_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="No digest is stored for that run.")
    stored["backend"] = backend_label()
    stored["live"] = False
    log.info("api: /api/digest/%s served to %s", run_id, user["email"])
    return stored

@app.get("/api/signals")
def signals(
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    region: str | None = Query(None, description="AU or PNG; omit for all"),
    cycle: str | None = Query(None, description="WEEKLY, MONTHLY or QUARTERLY"),
    source: str | None = Query(None, description="collector source; omit for all"),
    q: str | None = Query(None, description="free-text search across the row"),
    user: dict[str, Any] = Depends(require_user),
) -> dict:
    """The Signal Feed: every classified signal, paginated and filtered.

    Separate from /api/digest because the two answer different questions. The
    digest is a ranked, region-balanced selection from one week; this is the
    whole list, newest first, with no cap.
    """
    payload = build_feed_payload(
        limit=limit, offset=offset, region=region, cycle=cycle, source=source, q=q
    )
    log.info("api: /api/signals served to %s (%d-%d of %d)",
             user["email"], offset, offset + len(payload["signals"]), payload["total"])
    return payload

