"""ASX company announcements — the guide's "direct signal of mining/energy
company activity".

A contract award, a final investment decision or a new managing director is
announced to the market before it becomes a job advert, and often before the
trade press writes it up. This reads those announcements for a list of
companies Easy Skill recruits into.

**Where it reads from.** The same JSON service the ASX website's own
announcements pages call, one request per company. It needs no key. The older
`asx.com.au/asx/1/company/...` address the guide's "ASX API" refers to now
answers 404.

**What is kept.** Most of what a listed company lodges is housekeeping — a
director's interest notice, a daily buy-back update, a change in a substantial
holding. None of that says anything about hiring, and reading it would spend an
AI call per notice to learn so. An announcement is kept when the ASX itself
marks it price sensitive, or when its headline carries a word that means
something happened (a contract, a project, an appointment, results). The
housekeeping forms are dropped by name either way.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

SOURCE_NAME = "asx"
SOURCE_TYPE = "news"
GEOGRAPHY = "AU"

API = "https://asx.api.markitdigital.com/asx-research/1.0/companies/{ticker}/announcements"
#: The public page a reader is sent to. The fragment makes each announcement's
#: address unique, which is what ingest dedupes on.
PAGE = "https://www.asx.com.au/markets/company/{ticker}#announcement-{key}"

USER_AGENT = "Mozilla/5.0 (compatible; MIOS/0.2; +https://easyskill.com.au)"
REQUEST_TIMEOUT = 20
REQUEST_DELAY_SECONDS = 0.4
#: Announcements requested per company. A weekly run rarely needs more: a
#: company that lodged twenty things in a week lodged mostly housekeeping.
PER_COMPANY = 10

#: Miners, energy producers and the contractors that staff their projects.
#: A list set under Admin › Data sources replaces this one.
DEFAULT_TICKERS: tuple[str, ...] = (
    "BHP", "RIO", "FMG", "S32", "NEM", "NST", "EVN", "MIN", "PLS", "IGO", "LYC", "WHC",
    "WDS", "STO", "ORG", "BPT", "KAR",
    "DOW", "MND", "CVL", "NWH", "WOR", "PRN", "MAH",
)

#: Lodgements that are never a signal, whatever else the headline says.
HOUSEKEEPING = re.compile(
    r"appendix\s*(2a|3[a-z])\b|director'?s?\s+interest|substantial\s+hold|"
    r"ceasing\s+to\s+be|becoming\s+a\s+substantial|buy-?back|"
    r"unquoted\s+securities|application\s+for\s+quotation|cleansing\s+notice|"
    r"dividend|distribution|proposed\s+issue|section\s+708|"
    r"notice\s+of\s+(annual\s+)?general\s+meeting|proxy\s+form|"
    r"change\s+of\s+(registry|address|company\s+secretary)|"
    r"notification\s+(of|regarding)\b|trading\s+halt|reinstatement|"
    # Scheduling notices about a report, not the report: "Quarterly Results
    # Webcast Details" matched on "quarterly" and "results" and said nothing.
    r"webcast|teleconference|conference\s+call|\badvisory\b|"
    r"(release|reporting)\s+date|investor\s+(day|briefing)\s+details",
    re.IGNORECASE,
)

#: Headline words that mean something happened which could move hiring.
SIGNAL = re.compile(
    r"contract|award|project|acqui|merger|takeover|production|quarterly|results|"
    r"appoint|resign|ceo|managing\s+director|chief|expansion|approval|approved|"
    r"investment\s+decision|\bfid\b|offtake|joint\s+venture|feasibility|"
    r"resource|reserve|guidance|commission|construction|restart|closure|"
    r"workforce|redundan|ramp-?up|first\s+(ore|gas|production|gold|lithium)",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tickers() -> tuple[str, ...]:
    """The companies to follow: the list set under Admin › Data sources, else
    the built-in one."""
    from loader import source_config

    return source_config.asx_tickers() or DEFAULT_TICKERS


def is_signal(headline: str, price_sensitive: bool) -> bool:
    """Whether an announcement is worth a classifier call."""
    if HOUSEKEEPING.search(headline or ""):
        return False
    return bool(price_sensitive or SIGNAL.search(headline or ""))


def parse_announcements(payload: dict[str, Any], ticker: str) -> list[dict[str, Any]]:
    """The service's JSON for one company -> signal records. Pure, for tests."""
    data = (payload or {}).get("data") or {}
    company = str(data.get("displayName") or ticker).title()
    out: list[dict[str, Any]] = []
    for item in data.get("items") or []:
        headline = str(item.get("headline") or "").strip()
        key = str(item.get("documentKey") or "").strip()
        if not headline or not key:
            continue
        sensitive = bool(item.get("isPriceSensitive"))
        if not is_signal(headline, sensitive):
            continue
        kind = str(item.get("announcementType") or "").strip().title()
        raw_content = " | ".join(p for p in (
            headline,
            f"{company} (ASX: {ticker})",
            f"ASX announcement — {kind}" if kind else "ASX announcement",
            "marked price sensitive by the ASX" if sensitive else "",
        ) if p)
        out.append({
            "source_url": PAGE.format(ticker=ticker, key=key),
            "raw_content": raw_content,
            "captured_at": _now_iso(),
            "posted": item.get("date") or None,
            "title": headline,
            "publication": "ASX Announcements",
            "company": company,
            "source_name": SOURCE_NAME,
            "source_type": SOURCE_TYPE,
            "geography": GEOGRAPHY,
        })
    return out


def _scrape_sync(limit: int, base_url: str | None) -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    per_company: list[list[dict[str, Any]]] = []
    for i, ticker in enumerate(tickers()):
        if i:
            time.sleep(REQUEST_DELAY_SECONDS)
        url = (base_url or API).format(ticker=ticker)
        try:
            res = session.get(url, params={"count": PER_COMPANY}, timeout=REQUEST_TIMEOUT)
            if res.status_code == 400:
                # "Symbol not found": a delisted or mistyped code. Named, so the
                # list gets fixed, and not treated as the service being down.
                log.warning("%s: %s is not a listed code — skipping", SOURCE_NAME, ticker)
                per_company.append([])
                continue
            res.raise_for_status()
            per_company.append(parse_announcements(res.json(), ticker))
        except Exception as exc:  # noqa: BLE001 - one company, not the run
            log.warning("%s: %s unreachable (%s) — skipping", SOURCE_NAME, ticker, exc)
            per_company.append([])

    # Round-robin, so one busy company cannot use the whole limit.
    collected: list[dict[str, Any]] = []
    cursor = 0
    while len(collected) < limit and any(cursor < len(c) for c in per_company):
        for items in per_company:
            if len(collected) >= limit:
                break
            if cursor < len(items):
                collected.append(items[cursor])
        cursor += 1

    log.info("%s: %d announcements kept from %d companies",
             SOURCE_NAME, len(collected), len(per_company))
    return collected


async def scrape_async(limit: int = 50, base_url: str | None = None) -> list[dict[str, Any]]:
    """Never raises; returns [] on any failure, like the other sources.

    `base_url` replaces the service address and must contain `{ticker}`.
    """
    if base_url and "{ticker}" not in base_url:
        log.warning("%s: base URL must contain {ticker}", SOURCE_NAME)
        return []
    try:
        return await asyncio.to_thread(_scrape_sync, limit, base_url)
    except Exception as exc:  # noqa: BLE001
        log.error("%s: scrape failed (%s)", SOURCE_NAME, exc)
        return []
