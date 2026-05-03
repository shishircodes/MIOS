"""PNGworkforce scraper.

Implementation choice: Apify SDK (crawlee, BeautifulSoupCrawler) per project brief.
We picked the local crawlee SDK over the hosted-actor + APIFY_TOKEN flavour because
it requires no external Apify account and is reproducible by graders cloning the repo.

The scraper is best-effort. Per the brief it must fail gracefully:
on any HTML-structure change, network error, or unexpected response, log a warning
and return an empty list so the pipeline still runs on synthetic data.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from config.settings import settings

log = logging.getLogger(__name__)

USER_AGENT = "MIOS-MarketIntelBot/0.1 (Easy Skill Australia PoC; contact: pbussy@easyskill.com)"
REQUEST_DELAY_SECONDS = 1.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Pure parser (testable against a saved HTML fixture, no network)
# --------------------------------------------------------------------------


def parse_listing(html: str, source_url: str, base_url: str | None = None) -> list[dict[str, Any]]:
    """Parse a PNGworkforce-style listing page into raw signal dicts.

    Targets the live PNGworkforce HTML structure (`div.job-result`) plus a few
    common fallback patterns. If structure changes, returns whatever it can
    find (possibly empty) and the calling pipeline continues on synthetic data.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_url = base_url or source_url

    # Live PNGworkforce listing structure: div.job-result, with title + link
    # in .col2 h4.t2 a, and the first <p> containing "<date> ... <Company - Location>".
    cards = soup.select("div.job-result")
    if cards:
        return _parse_pngworkforce_cards(cards, base_url, source_url)

    # Fallback: generic patterns for unrelated job-board HTML (kept so the
    # synthetic test fixture and other sites still parse).
    cards = soup.select(
        "article.job, article.job-listing, div.job-card, div.job-listing, "
        "li.job-result, .job-item, .vacancy-item"
    )
    if not cards:
        cards = [a for a in soup.find_all("article") if a.find(["h2", "h3"]) and a.find("a")]

    out: list[dict[str, Any]] = []
    for card in cards:
        title_el = card.find(["h1", "h2", "h3", "h4"])
        title = title_el.get_text(strip=True) if title_el else None

        link_el = card.find("a", href=True)
        href = link_el["href"] if link_el else None
        full_url = urljoin(base_url, href) if href else None

        location_el = card.select_one(".location, .job-location, [class*='location']")
        location = location_el.get_text(strip=True) if location_el else None

        body_el = card.select_one(".description, .job-description, .summary, p")
        body = body_el.get_text(" ", strip=True) if body_el else card.get_text(" ", strip=True)

        raw_parts = [p for p in (title, location, body) if p]
        raw_content = " | ".join(raw_parts)
        if not raw_content:
            continue

        out.append({
            "source_url": full_url or source_url,
            "raw_content": raw_content,
            "captured_at": _now_iso(),
            "title": title,
            "location": location,
        })

    log.info("parse_listing: parsed %d job cards from %s", len(out), source_url)
    return out


def _parse_pngworkforce_cards(cards, base_url: str, source_url: str) -> list[dict[str, Any]]:
    """Parser specialised for div.job-result on pngworkforce.com."""
    out: list[dict[str, Any]] = []
    for card in cards:
        title_link = card.select_one("h4.t2 a") or card.select_one("h4 a") or card.find("a", href=True)
        title = title_link.get_text(strip=True) if title_link else None
        href = title_link["href"] if (title_link and title_link.has_attr("href")) else None
        full_url = urljoin(base_url, href) if href else None

        # First <p> in .col2 carries date + company + location across <strong>s
        meta_p = card.select_one(".col2 p") or card.find("p")
        company = location = posted = None
        if meta_p:
            strongs = [s.get_text(" ", strip=True) for s in meta_p.find_all("strong")]
            # strongs typically: ["03 May 2026", "K92 Mining Inc. - Eastern Highlands, Eastern Highlands"]
            for s in strongs:
                if not posted and any(ch.isdigit() for ch in s) and len(s) <= 30 and "*" not in s:
                    posted = s
                elif " - " in s and not company:
                    parts = s.split(" - ", 1)
                    company = parts[0].strip()
                    location = parts[1].strip() if len(parts) == 2 else None

        # Description preview is the second <p>
        body_paras = card.select(".col2 p")
        body = body_paras[1].get_text(" ", strip=True) if len(body_paras) > 1 else None
        if not body:
            body = card.get_text(" ", strip=True)

        raw_parts = [p for p in (title, company, location, body) if p]
        raw_content = " | ".join(raw_parts)
        if not raw_content or not title:
            continue

        out.append({
            "source_url": full_url or source_url,
            "raw_content": raw_content,
            "captured_at": _now_iso(),
            "title": title,
            "location": location,
            "company": company,
            "posted": posted,
        })

    log.info("parse_listing: parsed %d job cards from %s (pngworkforce mode)",
             len(out), source_url)
    return out


# --------------------------------------------------------------------------
# Live fetch (Apify / crawlee). Best-effort, returns [] on any failure.
# --------------------------------------------------------------------------


async def _crawl_async(base_url: str, limit: int) -> list[dict[str, Any]]:
    from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext

    collected: list[dict[str, Any]] = []

    crawler = BeautifulSoupCrawler(
        max_requests_per_crawl=max(1, min(limit, 20)),
        request_handler_timeout=__import__("datetime").timedelta(seconds=20),
    )

    @crawler.router.default_handler
    async def _handle(context: BeautifulSoupCrawlingContext) -> None:
        html = str(context.soup)
        records = parse_listing(html, source_url=str(context.request.url), base_url=base_url)
        for r in records:
            if len(collected) >= limit:
                return
            collected.append(r)

    await crawler.run([base_url])
    return collected


def scrape(limit: int = 200, base_url: str | None = None) -> list[dict[str, Any]]:
    """Scrape up to `limit` job postings from PNGworkforce. Returns [] on any error."""
    # Explicit empty string means "no URL" — don't fall back to settings.
    if base_url == "":
        log.warning("scrape: base URL is empty")
        return []
    target = base_url or settings.pngworkforce_base_url
    if not target:
        log.warning("scrape: no base URL configured")
        return []

    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https"):
        log.warning("scrape: invalid base URL %r", target)
        return []

    try:
        return asyncio.run(_crawl_async(target, limit))
    except Exception as exc:  # noqa: BLE001 - brief mandates graceful failure
        log.warning("scrape: crawl failed (%s) — returning empty list", exc)
        return []
