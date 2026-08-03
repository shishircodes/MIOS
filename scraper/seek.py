"""SEEK (au.seek.com) scraper.

Second live source alongside `scraper.pngworkforce`. Same contract: a pure
`parse_listing` that can be tested against a saved fixture, plus a best-effort
`scrape` that returns [] on any failure so the pipeline keeps running.

robots.txt constraints (au.seek.com, checked 2026-08-02) shape the design:

    User-agent: *
    Disallow: */job/          # job DETAIL pages are off limits
    Disallow: *?              # ANY url with a query string is off limits
    Disallow: /graphql, /api/jobsearch/

Two consequences we live with rather than work around:

1. We only fetch category landing paths (e.g. `/jobs-in-mining-resources-energy`),
   which are query-free and allowed. Everything we need — title, company,
   location, teaser, posted date, salary — is server-rendered on the card, so we
   never have to open a `/job/<id>` detail page.
2. SEEK paginates with `?page=2`, which is disallowed, so each path yields only
   its first page (~32 cards). Breadth comes from crawling several category
   paths, not from paging. See `DEFAULT_PATHS` / the SEEK_PATHS env var.

`_assert_allowed` enforces both rules at runtime so a future caller can't
accidentally point this at a disallowed URL.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from config.settings import settings

log = logging.getLogger(__name__)

USER_AGENT = "MIOS-MarketIntelBot/0.1 (Easy Skill Australia PoC; contact: pbussy@easyskill.com)"
REQUEST_DELAY_SECONDS = 1.0

SOURCE_NAME = "seek"
GEOGRAPHY = "AU"

#: Category landing paths crawled by default. Chosen to match the watchlist's
#: mining / energy / engineering skew. Override with SEEK_PATHS in .env.
DEFAULT_PATHS = (
    "/jobs-in-mining-resources-energy",
    "/jobs-in-engineering",
    "/jobs-in-construction",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RobotsDisallowed(ValueError):
    """Raised when a target URL is blocked by au.seek.com's robots.txt."""


def _assert_allowed(url: str) -> None:
    """Reject URLs that au.seek.com's robots.txt disallows for User-agent: *."""
    parsed = urlparse(url)
    if parsed.query:
        raise RobotsDisallowed(f"robots.txt disallows query strings (*?): {url}")
    if "/job/" in parsed.path:
        raise RobotsDisallowed(f"robots.txt disallows job detail pages (*/job/): {url}")
    if parsed.path.startswith(("/graphql", "/api/jobsearch/")):
        raise RobotsDisallowed(f"robots.txt disallows API paths: {url}")


def _canonical_job_url(href: str, base_url: str) -> str:
    """Absolute `/job/<id>` URL with tracking params stripped.

    Card links carry `?type=promoted&ref=search-standalone&origin=cardTitle`,
    which differs between the promoted and organic placements of the *same* job.
    Dropping the query gives a stable dedupe key for `signals.source_url`.
    We store this URL; we never fetch it (robots.txt disallows */job/).
    """
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    return urlunparse(parsed._replace(query="", fragment=""))


# --------------------------------------------------------------------------
# Pure parser (testable against a saved HTML fixture, no network)
# --------------------------------------------------------------------------


def parse_listing(html: str, source_url: str, base_url: str | None = None) -> list[dict[str, Any]]:
    """Parse a SEEK category listing page into raw signal dicts.

    Targets `article[data-testid="job-card"]` and the `data-automation` hooks
    SEEK puts on each field — these are their own test selectors, so they're
    considerably more stable than class names. Returns whatever it can find
    (possibly empty) if the structure changes; the pipeline carries on.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_url = base_url or source_url

    cards = soup.select('article[data-testid="job-card"]')
    if not cards:
        cards = soup.select('article[data-automation="normalJob"], article[data-automation="premiumJob"]')

    out: list[dict[str, Any]] = []
    for card in cards:
        title_link = card.select_one('a[data-automation="jobTitle"]')
        title = title_link.get_text(" ", strip=True) if title_link else None
        if not title:
            continue

        href = title_link.get("href")
        job_url = _canonical_job_url(href, base_url) if href else None

        company = _text(card, '[data-automation="jobCompany"]')
        location = _text(card, '[data-automation="jobCardLocation"]') or _text(
            card, '[data-automation="jobLocation"]'
        )
        body = _text(card, '[data-automation="jobShortDescription"]')
        posted = _text(card, '[data-automation="jobListingDate"]')
        salary = _text(card, '[data-automation="jobSalary"]')
        classification = _text(card, '[data-automation="jobSubClassification"]')

        # Salary and classification go into raw_content because the analyst
        # agent reads that blob — a "Superintendent, $250k" card is a much
        # stronger expansion signal than the title alone.
        raw_parts = [p for p in (title, company, location, classification, salary, body) if p]
        raw_content = " | ".join(raw_parts)
        if not raw_content:
            continue

        out.append({
            "source_url": job_url or source_url,
            "raw_content": raw_content,
            "captured_at": _now_iso(),
            "title": title,
            "location": location,
            "company": company,
            "posted": posted,
            "salary": salary,
            "source_name": SOURCE_NAME,
            "source_type": "job_board",
            "geography": GEOGRAPHY,
        })

    log.info("seek.parse_listing: parsed %d job cards from %s", len(out), source_url)
    return out


def _text(card, selector: str) -> str | None:
    el = card.select_one(selector)
    if el is None:
        return None
    return el.get_text(" ", strip=True) or None


# --------------------------------------------------------------------------
# Live fetch (Apify / crawlee). Best-effort, returns [] on any failure.
# --------------------------------------------------------------------------


async def _crawl_async(targets: list[str], base_url: str, limit: int) -> list[dict[str, Any]]:
    from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    crawler = BeautifulSoupCrawler(
        max_requests_per_crawl=max(1, min(len(targets), 20)),
        request_handler_timeout=timedelta(seconds=30),
    )

    @crawler.router.default_handler
    async def _handle(context: BeautifulSoupCrawlingContext) -> None:
        html = str(context.soup)
        records = parse_listing(html, source_url=str(context.request.url), base_url=base_url)
        for r in records:
            if len(collected) >= limit:
                return
            # A job can appear on more than one category page; dedupe here so the
            # limit isn't spent on repeats before ingest ever sees them.
            key = r["source_url"]
            if key in seen:
                continue
            seen.add(key)
            collected.append(r)

    await crawler.run(targets)
    return collected


async def scrape_async(
    limit: int = 200,
    base_url: str | None = None,
    paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Async core of `scrape`. Returns [] on any error.

    `scraper.scrape_all` awaits this directly so every source shares one event
    loop — crawlee binds global state (storage client locks) to the loop that
    first touches it, so a second `asyncio.run` in the same process fails.
    """
    # Explicit empty string means "no URL" — don't fall back to settings.
    if base_url == "":
        log.warning("seek.scrape: base URL is empty")
        return []
    base = base_url or settings.seek_base_url
    if not base:
        log.warning("seek.scrape: no base URL configured")
        return []

    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https"):
        log.warning("seek.scrape: invalid base URL %r", base)
        return []

    targets: list[str] = []
    for path in paths or settings.seek_paths or DEFAULT_PATHS:
        url = urljoin(base, path)
        try:
            _assert_allowed(url)
        except RobotsDisallowed as exc:
            log.warning("seek.scrape: skipping %s — %s", path, exc)
            continue
        targets.append(url)

    if not targets:
        log.warning("seek.scrape: no robots-allowed targets to crawl")
        return []

    try:
        return await _crawl_async(targets, base, limit)
    except Exception as exc:  # noqa: BLE001 - brief mandates graceful failure
        log.warning("seek.scrape: crawl failed (%s) — returning empty list", exc)
        return []


def scrape(
    limit: int = 200,
    base_url: str | None = None,
    paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Scrape up to `limit` postings from SEEK category pages. [] on any error.

    Sync entry point for single-source use (tests, ad-hoc runs).
    """
    return asyncio.run(scrape_async(limit=limit, base_url=base_url, paths=paths))
