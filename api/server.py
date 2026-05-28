"""FastAPI bridge between the MIOS Python pipeline and the TanStack web app.

Run:
    uvicorn api.server:app --reload --port 8787

Endpoints:
    GET /api/health   -> liveness + which data mode is active
    GET /api/digest   -> structured weekly digest (live SQLite, or synthetic fallback)
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.digest_service import build_digest_payload
from config.settings import configure_logging, settings

configure_logging()
log = logging.getLogger("api.server")

app = FastAPI(title="MIOS API", version="0.1.0")

# Allow the Vite dev server (and a couple of common ports) to call us in dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    payload = build_digest_payload()
    return {
        "status": "ok",
        "dataMode": payload["sourceMode"],
        "db": str(settings.db_path),
        "signals": len(payload["signals"]),
    }


@app.get("/api/digest")
def digest() -> dict:
    return build_digest_payload()
