"""Configuration loader. Reads .env and exposes a typed Settings object."""
from __future__ import annotations

import logging
import os
import secrets
import dataclasses
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True, repr=False)
class Settings:
    gemini_api_key: str
    gemini_model: str
    slack_webhook_url: str
    db_path: Path
    #: Neon/PostgreSQL DSN. When set it wins over db_path everywhere.
    database_url: str
    log_level: str
    apify_token: str
    pngworkforce_base_url: str
    seek_base_url: str
    seek_paths: tuple[str, ...]
    adzuna_app_id: str
    adzuna_app_key: str
    adzuna_country: str
    adzuna_queries: tuple[str, ...]
    news_feeds: tuple[str, ...]
    watchlist_path: Path
    # --- Google Sign-In (OAuth 2.0 / OIDC) ---
    google_client_id: str
    google_client_secret: str
    session_secret: str
    session_max_age: int
    oauth_redirect_uri: str
    web_app_url: str
    allowed_google_domain: str
    allowed_emails: tuple[str, ...]
    auth_disabled: bool
    #: Whether this process runs the weekly pipeline by itself. Off by default,
    #: and deliberately an environment variable rather than a database setting:
    #: it answers "is this the process that runs it?", not "should it run?".
    #: Every developer with a copy of the production DSN would otherwise start
    #: scraping and spending Gemini quota the moment they ran the API locally.
    scheduler_enabled: bool

    #: Fields whose value must never be printed. The default dataclass repr
    #: includes every field, so a single unhandled exception — or a failing test,
    #: which is how this was found — dumps the Gemini key, the Neon password and
    #: the session-signing secret into the log. CI logs are retained and, on a
    #: public repo, world-readable.
    _SECRET_FIELDS = frozenset({
        "gemini_api_key", "slack_webhook_url", "database_url", "apify_token",
        "adzuna_app_key", "google_client_secret", "session_secret",
    })

    def __repr__(self) -> str:
        shown = []
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if f.name in self._SECRET_FIELDS and value:
                # Whether a secret is *set* is worth knowing when debugging;
                # what it is never is.
                value = "<set>"
            shown.append(f"{f.name}={value!r}")
        return f"Settings({', '.join(shown)})"

    @property
    def ground_truth_path(self) -> Path:
        return REPO_ROOT / "data" / "ground_truth.jsonl"

    @property
    def synthetic_postings_path(self) -> Path:
        return REPO_ROOT / "data" / "synthetic_postings.jsonl"

    @property
    def adzuna_configured(self) -> bool:
        """True when the free Adzuna API credentials are present."""
        return bool(self.adzuna_app_id and self.adzuna_app_key)

    @property
    def oauth_configured(self) -> bool:
        """True when there are enough credentials to actually run the Google flow."""
        return bool(self.google_client_id and self.google_client_secret)


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_list(name: str) -> tuple[str, ...]:
    """Comma-separated env var -> tuple. Empty tuple means 'use the code default'."""
    return tuple(p.strip() for p in _get(name).split(",") if p.strip())


def _get_bool(name: str, default: bool = False) -> bool:
    raw = _get(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def load_settings() -> Settings:
    db_path = Path(_get("DB_PATH", "data/mios.db"))
    if not db_path.is_absolute():
        db_path = REPO_ROOT / db_path

    # No SESSION_SECRET? Mint a random one per process rather than shipping a default.
    # A committed fallback secret would let anyone forge a session cookie; the cost of
    # generating one is that sessions don't survive a server restart, which is fine in dev.
    session_secret = _get("SESSION_SECRET") or secrets.token_urlsafe(48)

    web_app_url = _get("WEB_APP_URL", "http://localhost:3000").rstrip("/")
    api_base_url = _get("API_BASE_URL", "http://localhost:8787").rstrip("/")

    return Settings(
        gemini_api_key=_get("GEMINI_API_KEY"),
        gemini_model=_get("GEMINI_MODEL", "gemini-2.5-flash"),
        slack_webhook_url=_get("SLACK_WEBHOOK_URL"),
        db_path=db_path,
        database_url=_get("DATABASE_URL"),
        log_level=_get("LOG_LEVEL", "INFO"),
        apify_token=_get("APIFY_TOKEN"),
        pngworkforce_base_url=_get("PNGWORKFORCE_BASE_URL", "https://www.pngworkforce.com"),
        seek_base_url=_get("SEEK_BASE_URL", "https://au.seek.com"),
        seek_paths=_get_list("SEEK_PATHS"),
        adzuna_app_id=_get("ADZUNA_APP_ID"),
        adzuna_app_key=_get("ADZUNA_APP_KEY"),
        adzuna_country=_get("ADZUNA_COUNTRY", "au"),
        adzuna_queries=_get_list("ADZUNA_QUERIES"),
        news_feeds=_get_list("NEWS_FEEDS"),
        watchlist_path=REPO_ROOT / "config" / "watchlist.json",
        google_client_id=_get("GOOGLE_CLIENT_ID"),
        google_client_secret=_get("GOOGLE_CLIENT_SECRET"),
        session_secret=session_secret,
        session_max_age=int(_get("SESSION_MAX_AGE", "43200")),  # 12h
        oauth_redirect_uri=_get("OAUTH_REDIRECT_URI", f"{api_base_url}/auth/callback"),
        web_app_url=web_app_url,
        allowed_google_domain=_get("ALLOWED_GOOGLE_DOMAIN"),
        allowed_emails=_get_list("ALLOWED_EMAILS"),
        auth_disabled=_get_bool("AUTH_DISABLED", False),
        scheduler_enabled=_get_bool("SCHEDULER_ENABLED", False),
    )


def configure_logging(level: str | None = None) -> None:
    logging.basicConfig(
        level=(level or _get("LOG_LEVEL", "INFO")).upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


settings = load_settings()
