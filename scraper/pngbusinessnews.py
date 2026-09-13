"""PNG Business News — scraped from its category listings.

This publication has no feed. `/feed/`, `/rss` and `/rss.xml` all answer 404,
and the site is a hand-built CMS rather than WordPress, so there is nothing to
subscribe to. Its coverage is close to the centre of what MIOS watches —
"Ok Tedi Declares K450 Million Interim Dividend", "K92 Mining posts US$84.6m Q2
profit", "Simberi Mining Project CDA Signed After Three Decades of Delays" — so
it is worth reading from the HTML.

Plain `requests` and BeautifulSoup rather than crawlee. The listings are static
server-rendered markup, so a browser engine buys nothing, and staying off
crawlee keeps this source free of the shared storage-client state that stops the
two crawlee scrapers running concurrently.

**An unknown category returns the home page, with status 200.** Verified:
`/articles/definitely-not-a-real-category-xyz` and `/articles/oil-gas` both
serve a byte-identical copy of `/`. A mistyped or renamed category would
therefore scrape the home page on every run, forever, and look healthy while
doing it — which is precisely how PNGworkforce came to collect nothing for weeks
while reporting success. `_looks_like_category` refuses that.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests


log = logging.getLogger(__name__)

SOURCE_NAME = "pngbusinessnews"
SOURCE_TYPE = "news"
GEOGRAPHY = "PNG"

BASE_URL = "https://www.pngbusinessnews.com"

#: The categories worth reading. The site also publishes agriculture,
#: commentary, events, finance and tourism; these four are the ones that carry
#: hiring and project signals.
#:
#: `oil-and-gas`, not `oil-gas`. The latter is a real-looking URL that silently
#: serves the home page, which is the trap this module guards against.
CATEGORIES: tuple[str, ...] = ("mining", "oil-and-gas", "energy", "company-news")

#: Sent on every request. The same honest bot form the feed reader uses: it
#: names MIOS and gives a contact, in the shape publishers' filters expect.
USER_AGENT = "Mozilla/5.0 (compatible; MIOS/0.2; +https://easyskill.com.au)"

REQUEST_TIMEOUT = 25
#: Between category pages. Four requests a week is nothing, but they arrive
#: back to back and there is no reason to make them look like a burst.
REQUEST_DELAY_SECONDS = 1.0

#: Article URLs are `/articles/<year>/<month>/<slug>`. The date is in the path
#: because the listing markup carries no `<time>` element — checked, there are
#: none on the page.
ARTICLE_PATH = re.compile(r"/articles/(\d{4})/(\d{1,2})/([^/?#]+)")

#: A headline shorter than this is a section label or a stray link, not an
#: article.
MIN_TITLE_CHARS = 18


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _looks_like_category(html: str, home_html: str | None) -> bool:
    """Whether this page is a real category listing rather than the home page.

    The site answers 200 for any path under `/articles/`, serving the home page
    for anything it does not recognise. Comparing against the home page is the
    only way to tell a renamed category from a working one, and the difference
    between collecting nothing and collecting the same twenty home-page links
    under four different names.
    """
    if home_html is None:
        return True
    return html.strip() != home_html.strip()


def parse_listing(html: str, category: str, base_url: str = BASE_URL) -> list[dict[str, Any]]:
    """Category HTML -> signal records. Pure, so it is testable from a fixture."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Headlines live in an <h3> wrapping the link. Anchors are absolute on this
    # site, so a relative-path match finds nothing — which is worth stating
    # because it is what a reasonable first attempt tries.
    for heading in soup.find_all(["h2", "h3", "h4"]):
        anchor = heading.find("a", href=True) or heading.find_parent("a", href=True)
        if anchor is None:
            continue
        href = urljoin(base_url, anchor["href"])
        match = ARTICLE_PATH.search(urlparse(href).path)
        if not match:
            continue
        title = _clean(heading.get_text(" ", strip=True))
        if len(title) < MIN_TITLE_CHARS or href in seen:
            continue
        seen.add(href)

        year, month, _slug = match.groups()
        out.append({
            "source_url": href,
            # The category is part of the signal: "filed under mining" tells the
            # classifier what the publication itself thinks this is about.
            "raw_content": " | ".join((title, "PNG Business News", category.replace("-", " "))),
            "captured_at": _now_iso(),
            #: From the URL, since the listing carries no date element. Day is
            #: unknown, so this is the month rather than a made-up day.
            "posted": f"{year}-{int(month):02d}",
            "title": title,
            "publication": "PNG Business News",
            "source_name": SOURCE_NAME,
            "source_type": SOURCE_TYPE,
            "geography": GEOGRAPHY,
        })

    return out


def _scrape_sync(limit: int, base_url: str) -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    # Fetched once, to recognise the catch-all. A failure here is not fatal: the
    # guard falls back to accepting pages, which is the behaviour without it.
    home_html: str | None = None
    try:
        home = session.get(base_url + "/", timeout=REQUEST_TIMEOUT)
        if home.ok:
            home_html = home.text
    except requests.RequestException as exc:
        log.warning("%s: could not read the home page for comparison (%s)", SOURCE_NAME, exc)

    per_category: list[list[dict[str, Any]]] = []
    for i, category in enumerate(CATEGORIES):
        if i:
            time.sleep(REQUEST_DELAY_SECONDS)
        url = f"{base_url}/articles/{category}"
        try:
            res = session.get(url, timeout=REQUEST_TIMEOUT)
            res.raise_for_status()
        except requests.RequestException as exc:
            log.warning("%s: %s unreachable (%s)", SOURCE_NAME, category, exc)
            per_category.append([])
            continue

        if not _looks_like_category(res.text, home_html):
            # Loud, because the page looked fine. This is the failure that hides.
            log.error(
                "%s: /articles/%s served the home page rather than a listing — the "
                "category has been renamed or removed. Collecting nothing from it "
                "rather than re-collecting the home page under its name.",
                SOURCE_NAME, category)
            per_category.append([])
            continue

        records = parse_listing(res.text, category, base_url)
        if not records:
            log.warning("%s: %s returned no articles — the listing markup may have "
                        "changed", SOURCE_NAME, category)
        per_category.append(records)

    # Round-robin, so one busy category cannot consume the whole limit. Same
    # reasoning as the feed reader.
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = 0
    while len(collected) < limit and any(cursor < len(c) for c in per_category):
        for items in per_category:
            if len(collected) >= limit:
                break
            if cursor >= len(items):
                continue
            rec = items[cursor]
            if rec["source_url"] in seen:
                continue
            seen.add(rec["source_url"])
            collected.append(rec)
        cursor += 1

    # No CrawlWatch here: it is built around crawlee's statistics object, and
    # this source is plain requests. The per-category logging above covers the
    # same ground — unreachable, served-the-home-page, and parsed-but-empty are
    # each reported as they happen, which is what that helper exists to surface.
    log.info("%s: %d articles from %d categories", SOURCE_NAME, len(collected), len(CATEGORIES))
    return collected


async def scrape_async(limit: int = 50, base_url: str | None = None) -> list[dict[str, Any]]:
    """Async core. Returns [] on any error.

    `requests` is blocking, so the work goes to a thread rather than stalling
    the loop the other sources are sharing.
    """
    if base_url == "":
        log.warning("%s: base URL is empty", SOURCE_NAME)
        return []
    target = (base_url or BASE_URL).rstrip("/")
    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https"):
        log.warning("%s: invalid base URL %r", SOURCE_NAME, target)
        return []

    try:
        return await asyncio.to_thread(_scrape_sync, limit, target)
    except Exception as exc:  # noqa: BLE001 - one dead source, not a dead run
        log.error("%s: scrape failed (%s)", SOURCE_NAME, exc)
        return []


def scrape(limit: int = 50, base_url: str | None = None) -> list[dict[str, Any]]:
    """Synchronous entry point, for the command line and tests."""
    return asyncio.run(scrape_async(limit=limit, base_url=base_url))
