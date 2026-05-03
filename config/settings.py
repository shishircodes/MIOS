"""Configuration loader. Reads .env and exposes a typed Settings object."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    slack_webhook_url: str
    db_path: Path
    log_level: str
    apify_token: str
    pngworkforce_base_url: str
    watchlist_path: Path

    @property
    def ground_truth_path(self) -> Path:
        return REPO_ROOT / "data" / "ground_truth.jsonl"

    @property
    def synthetic_postings_path(self) -> Path:
        return REPO_ROOT / "data" / "synthetic_postings.jsonl"


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_settings() -> Settings:
    db_path = Path(_get("DB_PATH", "data/mios.db"))
    if not db_path.is_absolute():
        db_path = REPO_ROOT / db_path
    return Settings(
        gemini_api_key=_get("GEMINI_API_KEY"),
        gemini_model=_get("GEMINI_MODEL", "gemini-2.5-flash"),
        slack_webhook_url=_get("SLACK_WEBHOOK_URL"),
        db_path=db_path,
        log_level=_get("LOG_LEVEL", "INFO"),
        apify_token=_get("APIFY_TOKEN"),
        pngworkforce_base_url=_get("PNGWORKFORCE_BASE_URL", "https://www.pngworkforce.com"),
        watchlist_path=REPO_ROOT / "config" / "watchlist.json",
    )


def configure_logging(level: str | None = None) -> None:
    logging.basicConfig(
        level=(level or _get("LOG_LEVEL", "INFO")).upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


settings = load_settings()
