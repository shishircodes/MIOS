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
    GET  /api/digest   -> structured weekly digest  [AUTH REQUIRED]
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

from fastapi import Depends, FastAPI, Query
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
from api.admin_api import router as admin_router
from api.publish_api import router as publish_router
from api.push_api import router as push_router
from api.watchlist_api import router as watchlist_router
from config.settings import configure_logging, settings
from loader.db import backend_label, close_pool

configure_logging()
log = logging.getLogger("api.server")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Defined below; resolved at call time, not at definition time.
    _warn_on_insecure_config()
    # No-op once an admin exists. Without it a fresh database has no admin and
    # no way to create one from inside the app.
    access.ensure_bootstrap_admin()
    yield
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
    # DELETE is here for /api/push/profiles/{id}: these rows describe real
    # people, so removing one has to be possible from the UI.
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
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
    log.info("api: /api/digest served to %s (window=%dd)", user["email"], days)
    payload = build_digest_payload(days=days)
    # Name the engine, never the DSN — a Neon connection string embeds the
    # password, so it must not reach the browser or a log line.
    payload["backend"] = backend_label()
    return payload

@app.get("/api/signals")
def signals(
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    region: str | None = Query(None, description="AU or PNG; omit for all"),
    cycle: str | None = Query(None, description="WEEKLY, MONTHLY or QUARTERLY"),
    q: str | None = Query(None, description="free-text search across the row"),
    user: dict[str, Any] = Depends(require_user),
) -> dict:
    """The Signal Feed: every classified signal, paginated and filtered.

    Separate from /api/digest because the two answer different questions. The
    digest is a ranked, region-balanced selection from one week; this is the
    whole list, newest first, with no cap.
    """
    payload = build_feed_payload(
        limit=limit, offset=offset, region=region, cycle=cycle, q=q
    )
    log.info("api: /api/signals served to %s (%d-%d of %d)",
             user["email"], offset, offset + len(payload["signals"]), payload["total"])
    return payload

