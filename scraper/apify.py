"""Job boards read through an Apify actor.

For Indeed, Jora, Glassdoor, LinkedIn Jobs and several smaller boards the
guide's access method is "Apify scraper": a hosted actor, run under Easy
Skill's Apify account, that returns the board's listings as data. Those boards
either forbid crawlers outright (Jora's robots.txt disallows everything) or
refuse them (Indeed answers 403), so MIOS does not read them directly. An actor
is the licensed route, and this is the one connector for all of them.

**It does nothing until it is configured.** A board runs only when both are set:

* `APIFY_TOKEN` — the account token.
* `APIFY_ACTORS` — which actor reads which board, as `source=actor` pairs, e.g.
  `indeed=misceres/indeed-scraper,jora=some-user/jora-scraper`.

An actor's own input (search terms, country, filters) differs per actor, so it
is passed through untouched from `APIFY_INPUTS`: one JSON object keyed by
source, e.g. `APIFY_INPUTS={"indeed":{"position":"mining","country":"AU"}}`.
One variable rather than one per board, because the deployment has to name
every variable it passes to the container. The per-run limit from the Admin
panel is sent as `maxItems` and as the dataset limit.

**Field names differ between actors too**, so each record is read by trying
the names actors commonly use for a title, an employer, a place and a link. A
listing with no title or no link is skipped rather than guessed at.

This has been exercised against recorded responses, not against a live actor:
no token was available when it was written. The first run with a real actor is
worth watching in the logs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import requests

from config.settings import settings

log = logging.getLogger(__name__)

SOURCE_TYPE = "job_board"
RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
#: Apify holds a synchronous run open for up to five minutes.
REQUEST_TIMEOUT = 310
MAX_DESCRIPTION_CHARS = 700

TITLE_KEYS = ("positionName", "title", "jobTitle", "job_title", "name", "position")
COMPANY_KEYS = ("company", "companyName", "company_name", "employer", "hiringOrganization",
                "organization")
LOCATION_KEYS = ("location", "jobLocation", "job_location", "place", "city", "formattedLocation")
URL_KEYS = ("url", "jobUrl", "job_url", "link", "jobLink", "applyUrl", "externalApplyLink")
DESCRIPTION_KEYS = ("description", "descriptionText", "snippet", "summary", "jobDescription")
POSTED_KEYS = ("postedAt", "datePosted", "postingDateParsed", "publishedAt", "date", "listedAt")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def actor_for(source_id: str) -> str | None:
    """The actor configured for a board, or None."""
    return (settings.apify_actors or {}).get(source_id) or None


def configured(source_id: str) -> tuple[bool, str | None]:
    """Whether a board can run, and what is missing if not."""
    if not settings.apify_token:
        return False, "Needs an Apify token (APIFY_TOKEN)."
    if not actor_for(source_id):
        return False, f"Needs an Apify actor: add {source_id}=user/actor to APIFY_ACTORS."
    return True, None


def _first(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, dict):
            # e.g. {"name": "BHP"} or {"city": "Perth", "country": "AU"}
            value = value.get("name") or value.get("city") or value.get("text") or ""
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value if v)
        if value:
            return _WS.sub(" ", _TAG.sub(" ", str(value))).strip()
    return ""


def parse_items(items: Any, *, source_id: str, label: str, geography: str) -> list[dict[str, Any]]:
    """An actor's dataset -> signal records. Pure, so it is testable."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        title = _first(item, TITLE_KEYS)
        url = _first(item, URL_KEYS)
        if not title or not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        company = _first(item, COMPANY_KEYS)
        location = _first(item, LOCATION_KEYS)
        description = _first(item, DESCRIPTION_KEYS)
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS].rsplit(" ", 1)[0] + "…"
        out.append({
            "source_url": url,
            "raw_content": " | ".join(p for p in (title, company, location, description) if p),
            "captured_at": _now_iso(),
            "posted": _first(item, POSTED_KEYS) or None,
            "title": title,
            "company": company,
            "location": location,
            "publication": label,
            "source_name": source_id,
            "source_type": SOURCE_TYPE,
            "geography": geography,
        })
    return out


def _actor_input(source_id: str, limit: int) -> dict[str, Any]:
    raw = os.environ.get("APIFY_INPUTS", "").strip()
    body: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            mine = parsed.get(source_id) if isinstance(parsed, dict) else None
            if isinstance(mine, dict):
                body = dict(mine)
            elif not isinstance(parsed, dict):
                log.warning("apify: APIFY_INPUTS is not a JSON object — ignored")
        except ValueError as exc:
            log.warning("apify: APIFY_INPUTS is not valid JSON (%s) — ignored", exc)
    # The run limit wins over whatever the stored input says, so the Admin
    # panel's number is the one that applies.
    body["maxItems"] = limit
    return body


def _scrape_sync(source_id: str, label: str, geography: str, limit: int) -> list[dict[str, Any]]:
    actor = actor_for(source_id)
    res = requests.post(
        # Apify writes "user/actor" as "user~actor" in a path.
        RUN_URL.format(actor=str(actor).replace("/", "~")),
        params={"limit": limit, "clean": "true"},
        # The token goes in a header, never the URL, so it cannot end up in a log.
        headers={"Authorization": f"Bearer {settings.apify_token}"},
        json=_actor_input(source_id, limit),
        timeout=REQUEST_TIMEOUT,
    )
    res.raise_for_status()
    records = parse_items(res.json(), source_id=source_id, label=label, geography=geography)
    log.info("apify: %s (%s) returned %d listings", source_id, actor, len(records))
    return records[:limit]


async def scrape_async(source, limit: int = 50, base_url: str | None = None) -> list[dict[str, Any]]:
    """Run one board's actor. Never raises; returns [] on any failure.

    `source` is the catalogue entry. `base_url` has no meaning for an actor and
    is accepted only so every source has the same signature.
    """
    ok, missing = configured(source.id)
    if not ok:
        log.warning("apify: %s skipped — %s", source.id, missing)
        return []
    try:
        return await asyncio.to_thread(_scrape_sync, source.id, source.label,
                                       source.geography, limit)
    except Exception as exc:  # noqa: BLE001
        log.error("apify: %s failed (%s)", source.id, exc)
        return []
