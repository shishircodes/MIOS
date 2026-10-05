"""EU-funded tenders for Papua New Guinea, from TED (Tenders Electronic Daily).

The guide lists "EU International Partnerships — project tenders,
infrastructure programs" with TED as the way in. TED is the EU's official
tender journal; its search service is public and needs no key.

The volume is small — a handful of notices a year name Papua New Guinea as the
place of performance — which is the right size for a source the guide files
under later phases. It is here so that a notice which does appear is seen.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

SOURCE_NAME = "ted"
SOURCE_TYPE = "tender"
GEOGRAPHY = "PNG"

SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
NOTICE_PAGE = "https://ted.europa.eu/en/notice/-/detail/{number}"
#: TED's own query language. PNG is the ISO code for the place of performance.
QUERY = "place-of-performance = PNG SORT BY publication-date DESC"
FIELDS = ["notice-title", "publication-date", "buyer-name", "notice-type",
          "deadline-receipt-tender-date-lot"]

USER_AGENT = "Mozilla/5.0 (compatible; MIOS/0.2; +https://easyskill.com.au)"
REQUEST_TIMEOUT = 25
#: TED refuses a page larger than this.
MAX_ROWS = 100

NOTICE_KINDS = {
    "cn-standard": "contract notice", "cn-social": "contract notice",
    "can-standard": "contract award", "can-social": "contract award",
    "pin-only": "prior information notice", "veat": "award without prior notice",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _english(value: Any) -> str:
    """TED returns text keyed by language, each a string or a list of strings.

    English where there is any; otherwise the first language offered, because a
    French title is still a title.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return _english(value[0]) if value else ""
    if isinstance(value, dict):
        for key in ("eng", "ENG", "en"):
            if value.get(key):
                return _english(value[key])
        for v in value.values():
            text = _english(v)
            if text:
                return text
    return ""


def parse_notices(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """TED's search JSON -> signal records. Pure, for tests."""
    out: list[dict[str, Any]] = []
    for n in (payload or {}).get("notices") or []:
        number = str(n.get("publication-number") or "").strip()
        if not number:
            continue
        title = _english(n.get("notice-title"))
        buyer = _english(n.get("buyer-name"))
        if not (title or buyer):
            continue
        kind = NOTICE_KINDS.get(str(n.get("notice-type") or "").lower(), "tender notice")
        deadline = _english(n.get("deadline-receipt-tender-date-lot"))[:10]
        published = str(n.get("publication-date") or "")[:10]
        raw_content = " | ".join(p for p in (
            title or f"EU {kind}",
            buyer,
            f"EU {kind} (TED), Papua New Guinea",
            f"tenders due {deadline}" if deadline else "",
        ) if p)
        out.append({
            "source_url": NOTICE_PAGE.format(number=number),
            "raw_content": raw_content,
            "captured_at": _now_iso(),
            "posted": published or None,
            "title": title or f"EU {kind} {number}",
            "publication": "TED",
            "agency": buyer,
            "category": kind,
            "closes": deadline or None,
            "source_name": SOURCE_NAME,
            "source_type": SOURCE_TYPE,
            "geography": GEOGRAPHY,
        })
    return out


def _scrape_sync(limit: int, url: str) -> list[dict[str, Any]]:
    res = requests.post(
        url, timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        json={"query": QUERY, "fields": FIELDS, "limit": max(1, min(limit, MAX_ROWS))},
    )
    res.raise_for_status()
    records = parse_notices(res.json())[:limit]
    log.info("%s: %d notices", SOURCE_NAME, len(records))
    return records


async def scrape_async(limit: int = 50, base_url: str | None = None) -> list[dict[str, Any]]:
    """Never raises; returns [] on any failure, like the other sources."""
    try:
        return await asyncio.to_thread(_scrape_sync, limit, base_url or SEARCH_URL)
    except Exception as exc:  # noqa: BLE001
        log.error("%s: scrape failed (%s)", SOURCE_NAME, exc)
        return []
