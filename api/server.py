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
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from api.auth import check_google_client_id, require_user, router as auth_router
from api.digest_service import DEFAULT_WINDOW_DAYS, build_digest_payload
from config.settings import configure_logging, settings
from loader.db import backend_label

configure_logging()
log = logging.getLogger("api.server")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Defined below; resolved at call time, not at definition time.
    _warn_on_insecure_config()
    yield


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
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(auth_router)


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
