"""Mining People International — the guide's specialist mining job board.

The address in the guide (miningpeople.com.au) now redirects to
mpirecruitment.au, whose robots.txt allows everything. Its job search is plain
HTML: one card per vacancy with a category, a title and a short pitch.

It is a recruitment agency's own board, so the adverts rarely name the
employer. They are still worth reading for what the guide wants from this
source — which mining roles are in demand, and where — and the classifier
treats an agency-posted advert as the agency's, exactly as it does on the
other boards.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

SOURCE_NAME = "miningpeople"
SOURCE_TYPE = "job_board"
GEOGRAPHY = "AU"
BOARD = "Mining People International"

BASE_URL = "https://www.mpirecruitment.au"
RESULTS_PATH = "/Job/Results"
JOB_LINK = re.compile(r"^/job/details/\d+", re.IGNORECASE)

USER_AGENT = "Mozilla/5.0 (compatible; MIOS/0.2; +https://easyskill.com.au)"
REQUEST_TIMEOUT = 25
_WS = re.compile(r"\s+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(el) -> str:
    return _WS.sub(" ", el.get_text(" ", strip=True)).strip() if el is not None else ""


def parse_listing(html: str, base_url: str = BASE_URL) -> list[dict[str, Any]]:
    """The results page -> signal records. Pure, so it is testable from a fixture."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in soup.select("div.job-card"):
        link = card.find("a", href=JOB_LINK)
        if link is None:
            continue
        url = urljoin(base_url, link["href"])
        title = _text(link)
        if not title or url in seen:
            continue
        seen.add(url)
        category = _text(card.find("small"))
        pitch = _text(card.find("p"))
        raw_content = " | ".join(p for p in (title, BOARD, category, "Australia", pitch) if p)
        out.append({
            "source_url": url,
            "raw_content": raw_content,
            "captured_at": _now_iso(),
            "posted": None,
            "title": title,
            "company": BOARD,
            "location": "Australia",
            "category": category,
            "source_name": SOURCE_NAME,
            "source_type": SOURCE_TYPE,
            "geography": GEOGRAPHY,
        })
    return out


def _scrape_sync(limit: int, base_url: str) -> list[dict[str, Any]]:
    res = requests.get(f"{base_url}{RESULTS_PATH}", timeout=REQUEST_TIMEOUT,
                       headers={"User-Agent": USER_AGENT})
    res.raise_for_status()
    records = parse_listing(res.text, base_url)
    if not records:
        # Telling "no vacancies today" from "the markup changed": the second is
        # a broken scraper wearing the first one's face.
        if "job-card" not in res.text:
            log.error("%s: the results page carried no job cards — the markup has changed",
                      SOURCE_NAME)
        else:
            log.warning("%s: job cards were present but none could be read", SOURCE_NAME)
    log.info("%s: %d vacancies", SOURCE_NAME, len(records))
    return records[:limit]


async def scrape_async(limit: int = 50, base_url: str | None = None) -> list[dict[str, Any]]:
    """Never raises; returns [] on any failure, like the other sources."""
    target = (base_url or BASE_URL).rstrip("/")
    if urlparse(target).scheme not in ("http", "https"):
        log.warning("%s: invalid base URL %r", SOURCE_NAME, target)
        return []
    try:
        return await asyncio.to_thread(_scrape_sync, limit, target)
    except Exception as exc:  # noqa: BLE001
        log.error("%s: scrape failed (%s)", SOURCE_NAME, exc)
        return []
