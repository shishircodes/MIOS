"""Job boards read through an Apify actor.

For SEEK, Indeed, Jora, Glassdoor, LinkedIn Jobs and several smaller boards
the access method is an Apify actor: a hosted reader, run under Easy Skill's
Apify account, that returns the board's listings as data. Those boards either
forbid crawlers outright (Jora's robots.txt disallows everything) or refuse
them (Indeed answers 403, and SEEK answers 403 to the deployed server's
address), so MIOS does not read them directly. This is the one connector for
all of them.

What an actor fetches, and how, is the actor's business and not this module's:
it is not bound by the limits MIOS keeps to when it reads a site itself. Which
actor to trust with a board is the administrator's choice when naming it.

**It does nothing until it is configured.** A board runs only when it has both
an account token and an actor named for it. Both are set under Admin ›
Integrations (see `loader.source_config`), and nowhere else.

An actor's own input (search terms, country, filters) differs per actor, so it
is passed through untouched, as a JSON object entered beside the actor.

**Every run is capped twice, by Apify, whatever the actor is.** An actor is
somebody else's program on a metered account, so neither cap relies on it
co-operating:

* `maxItems` as a run option is the board's limit from the Admin panel. Apify
  will not charge a pay-per-result actor for more results than that.
* `maxTotalChargeUsd` is the administrator's ceiling on one run's cost, for
  every pricing model.

The limit is also put into the actor's input as `maxItems`, which the actors
that use that name read as "stop here", and it is the dataset limit, so MIOS
never keeps more than it either. An actor that names its count differently may
still fetch more than MIOS keeps; the caps above bound what that can cost.

**Field names differ between actors too**, so each record is read by trying
the names actors commonly use for a title, an employer, a place and a link. A
listing with no title or no link is skipped rather than guessed at.

This has been exercised against recorded responses, not against a live actor:
no token was available when it was written. The first run with a real actor is
worth watching in the logs.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import requests

from loader import source_config

log = logging.getLogger(__name__)

SOURCE_TYPE = "job_board"
RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
#: Apify holds a synchronous run open for up to five minutes.
REQUEST_TIMEOUT = 310
MAX_DESCRIPTION_CHARS = 700

TITLE_KEYS = ("positionName", "title", "jobTitle", "job_title", "name", "position")
COMPANY_KEYS = ("company", "companyName", "company_name", "employer", "advertiser",
                "hiringOrganization", "organization")
LOCATION_KEYS = ("location", "jobLocation", "job_location", "place", "city", "formattedLocation")
URL_KEYS = ("url", "jobUrl", "job_url", "link", "jobLink", "applyUrl", "externalApplyLink")
DESCRIPTION_KEYS = ("description", "descriptionText", "snippet", "summary", "jobDescription",
                    "teaser")
POSTED_KEYS = ("postedAt", "postedDate", "datePosted", "postingDateParsed", "publishedAt",
               "listingDate", "date", "listedAt")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def actor_for(source_id: str, target=None) -> str | None:
    """The actor configured for a board, or None."""
    return source_config.apify_actor(source_id, target) or None


def configured(source_id: str, target=None) -> tuple[bool, str | None]:
    """Whether a board can run, and what is missing if not."""
    if not source_config.apify_token(target):
        return False, "Needs an Apify token. Add one under Integrations."
    if not actor_for(source_id, target):
        return False, "Needs an Apify actor. Name one under Integrations."
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
    body = source_config.apify_input(source_id)
    # The run limit wins over whatever the stored input says, so the Admin
    # panel's number is the one that applies.
    body["maxItems"] = limit
    return body


def _scrape_sync(source_id: str, label: str, geography: str, limit: int) -> list[dict[str, Any]]:
    actor = actor_for(source_id)
    res = requests.post(
        # Apify writes "user/actor" as "user~actor" in a path.
        RUN_URL.format(actor=str(actor).replace("/", "~")),
        params={
            "limit": limit, "clean": "true",
            # Run options, enforced by Apify and not by the actor.
            "maxItems": limit,
            "maxTotalChargeUsd": source_config.apify_max_charge(),
        },
        # The token goes in a header, never the URL, so it cannot end up in a log.
        headers={"Authorization": f"Bearer {source_config.apify_token()}"},
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
