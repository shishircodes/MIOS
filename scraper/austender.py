"""AusTender — current approaches to market from the Commonwealth.

A tender is a hiring signal before a job advert is. An agency approaching the
market for a building contract is an organisation that will shortly need people
to deliver it, which is earlier than the vacancy this pipeline would otherwise
see months later.

**Why the live site and not the open data.** The Department of Finance publishes
contract notices on data.gov.au under CC-BY, which would be the better route if
it were current. It is not: that dataset was last updated in January 2024 and
its newest contract file covers 2019-20. For a pipeline whose question is "who
is hiring now", six-year-old awards are history rather than intelligence. The
`/Atm` list is served fresh — close dates on it are days away — so that is what
this reads.

**What robots.txt permits.** tenders.gov.au disallows `/Search/*`, `/Reports/*`,
`/Cn/List*`, `/Son/List*` and `/admin*`. `/Atm` is not among them, and it is the
only path this touches. The contract-notice lists — which are disallowed — are
deliberately left alone even though they carry similar information.

**No model call is needed to decide relevance.** AusTender categorises every
notice itself, from a UNSPSC-derived vocabulary, so filtering to construction,
engineering, mining and defence is a string match against the publisher's own
label rather than a judgement. Of 75 current notices sampled, 23 were relevant:
the rest were crop production, cloud software and office furniture.
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

SOURCE_NAME = "austender"
SOURCE_TYPE = "tender"
GEOGRAPHY = "AU"

BASE_URL = "https://www.tenders.gov.au"
#: The current approach-to-market list. Not `/Cn/List`, which robots.txt
#: disallows.
ATM_PATH = "/Atm"

USER_AGENT = "Mozilla/5.0 (compatible; MIOS/0.2; +https://easyskill.com.au)"

REQUEST_TIMEOUT = 25
REQUEST_DELAY_SECONDS = 1.0
#: Pages of fifteen. Four is a fortnight of notices at the rate the list moves,
#: which is more than a weekly run needs and keeps this to four requests.
MAX_PAGES = 4

#: Words in AusTender's own category that mean the work is the kind Easy Skill
#: recruits for. Matched against the publisher's label, so this is a lookup
#: rather than a judgement — which is the whole reason this source costs no
#: model calls.
#:
#: Taken from the categories actually present on the list rather than from the
#: UNSPSC vocabulary in the abstract. "Building construction and support and
#: maintenance and repair services" is the single largest category on it.
RELEVANT_CATEGORY_WORDS: tuple[str, ...] = (
    "construction", "building", "engineering", "architectural", "mining",
    "civil", "infrastructure", "roads", "rail", "marine", "dredging",
    "electrical", "mechanical", "plumbing", "maintenance", "repair",
    "installation", "plant", "equipment", "drilling", "earthmoving",
    "energy", "power", "fuel", "petroleum", "pipeline", "water", "utilities",
    "defence", "military", "shipbuilding", "aerospace",
    "project management", "facilities",
)

#: Categories that contain a relevant word but are not relevant work. Without
#: these, "General building and office cleaning and maintenance services"
#: arrives as a construction signal on the strength of "building", and
#: "Computer hardware maintenance and support" on the strength of "maintenance"
#: — both observed on the live list.
EXCLUDED_CATEGORY_WORDS: tuple[str, ...] = (
    "cleaning", "office furniture", "stationery", "catering", "travel",
    "insurance", "recruitment services", "legal", "audit", "advertising",
    "computer", "software", "hardware", "information technology",
    "telecommunication", "data services", "cloud",
)

ATM_LINK = re.compile(r"/Atm/Show/", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def is_relevant(category: str) -> bool:
    """Whether AusTender's own category describes work Easy Skill recruits for.

    Exclusions are checked first: "General building and office cleaning and
    maintenance services" carries three relevant words and is a cleaning
    contract.
    """
    lowered = (category or "").lower()
    if not lowered:
        return False
    if any(word in lowered for word in EXCLUDED_CATEGORY_WORDS):
        return False
    return any(word in lowered for word in RELEVANT_CATEGORY_WORDS)


def _row_fields(row: Any) -> dict[str, str]:
    """The labelled fields of one notice.

    Read by label rather than by position. AusTender renders each field as a
    `div.list-desc` holding a `<span>Label:</span>` and a `div.list-desc-inner`
    value, so a reordered or newly-inserted field costs nothing here, where an
    index would silently shift every value by one.
    """
    out: dict[str, str] = {}
    for block in row.select("div.list-desc"):
        label = block.find("span")
        inner = block.select_one("div.list-desc-inner")
        if label is None or inner is None:
            continue
        key = _clean(label.get_text(" ", strip=True)).rstrip(":")
        if key:
            out[key] = _clean(inner.get_text(" ", strip=True))
    return out


def parse_listing(html: str, base_url: str = BASE_URL) -> list[dict[str, Any]]:
    """ATM list HTML -> signal records. Pure, so it is testable from a fixture.

    Irrelevant categories are dropped here rather than downstream: the point of
    this source is that its relevance is already decided by the publisher, and
    passing office-furniture tenders to a model to be rejected would spend the
    allowance this source exists to save.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    skipped = 0

    for row in soup.select("div.box.listInner"):
        fields = _row_fields(row)
        atm_id = fields.get("ATM ID") or ""
        title = fields.get("Title") or fields.get("Description") or ""
        agency = fields.get("Agency") or ""
        category = fields.get("Category") or ""
        closes = fields.get("Close Date & Time") or ""

        anchor = row.find("a", href=ATM_LINK)
        url = urljoin(base_url, anchor["href"]) if anchor else ""
        if not url or not (atm_id or title):
            continue

        if not is_relevant(category):
            skipped += 1
            continue

        # The agency and category are part of the signal. An approach to market
        # is only intelligence once you know who is approaching and for what.
        raw_content = " | ".join(p for p in (
            title or atm_id, agency, category,
            f"closes {closes}" if closes else "",
        ) if p)

        out.append({
            "source_url": url,
            "raw_content": raw_content,
            "captured_at": _now_iso(),
            "posted": fields.get("Last Updated") or None,
            "title": title or atm_id,
            "publication": "AusTender",
            "agency": agency,
            "category": category,
            "closes": closes or None,
            "source_name": SOURCE_NAME,
            "source_type": SOURCE_TYPE,
            "geography": GEOGRAPHY,
        })

    if skipped:
        log.info("%s: kept %d notices, skipped %d outside the relevant categories",
                 SOURCE_NAME, len(out), skipped)
    return out


def _scrape_sync(limit: int, base_url: str) -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    any_rows = False

    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            time.sleep(REQUEST_DELAY_SECONDS)
        url = f"{base_url}{ATM_PATH}" + (f"?page={page}" if page > 1 else "")
        try:
            res = session.get(url, timeout=REQUEST_TIMEOUT)
            res.raise_for_status()
        except requests.RequestException as exc:
            log.warning("%s: page %d unreachable (%s)", SOURCE_NAME, page, exc)
            break

        records = parse_listing(res.text, base_url)
        # Distinguishing "this page held nothing relevant" from "the markup
        # changed and nothing parses" — the second is a broken scraper wearing
        # the first one's face.
        if "listInner" not in res.text:
            log.error("%s: page %d carried no notice rows — the list markup has "
                      "changed", SOURCE_NAME, page)
            break
        any_rows = True

        for rec in records:
            if rec["source_url"] in seen:
                continue
            seen.add(rec["source_url"])
            collected.append(rec)
            if len(collected) >= limit:
                break
        if len(collected) >= limit:
            break

    if any_rows and not collected:
        log.warning("%s: notices were read but none were in a relevant category",
                    SOURCE_NAME)
    log.info("%s: %d relevant notices", SOURCE_NAME, len(collected))
    return collected


async def scrape_async(limit: int = 50, base_url: str | None = None) -> list[dict[str, Any]]:
    """Async core. Returns [] on any error."""
    if base_url == "":
        log.warning("%s: base URL is empty", SOURCE_NAME)
        return []
    target = (base_url or BASE_URL).rstrip("/")
    if urlparse(target).scheme not in ("http", "https"):
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
