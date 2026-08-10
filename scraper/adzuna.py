"""Adzuna scraper — a documented JSON API rather than HTML scraping.

Third source alongside `pngworkforce` and `seek`, and deliberately the simplest
of the three. The other two parse HTML that the site owner may restructure at
any time and whose terms restrict automated access; Adzuna publishes an API
*intended* to be called, with a free key. That removes the three things that
made SEEK awkward:

  * no robots.txt carve-outs to honour — this endpoint exists to be requested
  * no HTML selectors to break when a page is redesigned
  * no terms-of-use tension: programmatic access is the product

It also searches by keyword, so unlike SEEK's category browse it can ask
directly about watchlist companies. "Is BHP hiring in Australia this week?" is a
far stronger signal than "here are 32 mining jobs", and it feeds the hiring
velocity table without relying on Gemini to recover the employer from prose.

Get a free app_id/app_key at https://developer.adzuna.com/ and set
ADZUNA_APP_ID / ADZUNA_APP_KEY. Without them this source logs a warning and
returns [], so the pipeline still runs on the other two.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import requests

from config.settings import settings

log = logging.getLogger(__name__)

SOURCE_NAME = "adzuna"
GEOGRAPHY = "AU"

API_ROOT = "https://api.adzuna.com/v1/api/jobs"
REQUEST_TIMEOUT = 20
#: Adzuna's free tier is rate limited; a small gap keeps bursts civil.
REQUEST_DELAY_SECONDS = 0.5
#: Adzuna caps this server-side; asking for more is silently truncated.
MAX_RESULTS_PER_PAGE = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def watchlist_queries() -> list[str]:
    """Company names to search for, from the watchlist.

    Searching per company is the point of this source: it turns the watchlist
    into targeted queries instead of hoping a broad category browse happens to
    surface those employers.
    """
    try:
        entries = json.loads(settings.watchlist_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a missing watchlist must not break the run
        log.warning("adzuna: could not read watchlist (%s)", exc)
        return []
    return [e["company_name"] for e in entries if e.get("company_name")]


# --------------------------------------------------------------------------
# Pure parser (testable against a saved JSON fixture, no network)
# --------------------------------------------------------------------------


def _canonical_url(raw: str | None, job_id: str | None) -> str | None:
    """Stable URL for dedupe.

    `redirect_url` carries per-request tracking parameters, so the same job
    fetched twice would otherwise look like two rows. Stripping the query gives
    a URL that stays constant across runs.
    """
    if raw:
        parsed = urlparse(raw)
        if parsed.scheme in ("http", "https"):
            return urlunparse(parsed._replace(query="", fragment=""))
    return f"https://www.adzuna.com.au/details/{job_id}" if job_id else None


def parse_results(payload: dict[str, Any], source_url: str = API_ROOT) -> list[dict[str, Any]]:
    """Turn one Adzuna search response into raw signal dicts.

    Tolerates missing fields throughout: Adzuna omits keys rather than sending
    nulls, and a partial record is still a usable signal.
    """
    results = payload.get("results")
    if not isinstance(results, list):
        log.warning("adzuna: response had no results list")
        return []

    out: list[dict[str, Any]] = []
    for job in results:
        if not isinstance(job, dict):
            continue
        title = (job.get("title") or "").strip()
        if not title:
            continue

        company = ((job.get("company") or {}).get("display_name") or "").strip() or None
        location = ((job.get("location") or {}).get("display_name") or "").strip() or None
        category = ((job.get("category") or {}).get("label") or "").strip() or None
        body = (job.get("description") or "").strip() or None

        salary = None
        lo, hi = job.get("salary_min"), job.get("salary_max")
        if lo and hi:
            # salary_is_predicted means Adzuna inferred it rather than the ad
            # stating it; label it so the classifier doesn't treat it as fact.
            predicted = str(job.get("salary_is_predicted", "0")) == "1"
            salary = f"${int(lo):,} – ${int(hi):,}{' (estimated)' if predicted else ''}"

        raw_parts = [p for p in (title, company, location, category, salary, body) if p]
        raw_content = " | ".join(raw_parts)
        if not raw_content:
            continue

        job_id = str(job.get("id")) if job.get("id") is not None else None
        out.append({
            "source_url": _canonical_url(job.get("redirect_url"), job_id) or source_url,
            "raw_content": raw_content,
            # Adzuna reports when the ad was *posted*; the other scrapers only
            # know when we looked. Keep both rather than losing the real date.
            "captured_at": _now_iso(),
            "posted": job.get("created"),
            "title": title,
            "company": company,
            "location": location,
            "salary": salary,
            "source_name": SOURCE_NAME,
            "source_type": "job_board",
            "geography": GEOGRAPHY,
        })

    log.info("adzuna.parse_results: parsed %d jobs", len(out))
    return out


# --------------------------------------------------------------------------
# Live fetch. Best-effort: returns [] on any failure.
# --------------------------------------------------------------------------


def _search_once(query: str, per_page: int) -> list[dict[str, Any]]:
    """One keyword search. Raises on transport errors; the caller decides."""
    params = {
        "app_id": settings.adzuna_app_id,
        "app_key": settings.adzuna_app_key,
        "what": query,
        "results_per_page": min(per_page, MAX_RESULTS_PER_PAGE),
        "content-type": "application/json",
    }
    url = f"{API_ROOT}/{settings.adzuna_country}/search/1?{urlencode(params)}"
    res = requests.get(url, timeout=REQUEST_TIMEOUT)
    res.raise_for_status()
    return parse_results(res.json(), source_url=url)


def _scrape_sync(limit: int, queries: list[str]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    for i, query in enumerate(queries):
        if len(collected) >= limit:
            break
        if i:
            time.sleep(REQUEST_DELAY_SECONDS)
        try:
            records = _search_once(query, per_page=min(limit, MAX_RESULTS_PER_PAGE))
        except Exception as exc:  # noqa: BLE001
            # One bad query must not lose the queries that already succeeded.
            log.warning("adzuna: query %r failed (%s) — skipping", query, exc)
            continue

        for r in records:
            if len(collected) >= limit:
                break
            # The same job can match several company queries.
            key = r["source_url"]
            if key in seen:
                continue
            seen.add(key)
            r["query"] = query
            collected.append(r)

    log.info("adzuna: %d jobs from %d queries", len(collected), len(queries))
    return collected


async def scrape_async(
    limit: int = 200,
    base_url: str | None = None,
    queries: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Search Adzuna for up to `limit` postings. Returns [] on any error.

    `base_url` is accepted for registry compatibility and ignored — this source
    has a fixed API endpoint rather than a browsable site.
    """
    if not settings.adzuna_configured:
        log.warning(
            "adzuna: ADZUNA_APP_ID / ADZUNA_APP_KEY not set — skipping this source. "
            "Get a free key at https://developer.adzuna.com/"
        )
        return []

    selected = queries or list(settings.adzuna_queries) or watchlist_queries()
    if not selected:
        log.warning("adzuna: no queries to run")
        return []

    try:
        # requests is blocking; run it off the event loop so the shared loop the
        # registry uses is not stalled while these calls are in flight.
        return await asyncio.to_thread(_scrape_sync, limit, selected)
    except Exception as exc:  # noqa: BLE001 - brief mandates graceful failure
        log.warning("adzuna: search failed (%s) — returning empty list", exc)
        return []


def scrape(
    limit: int = 200,
    base_url: str | None = None,
    queries: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Sync entry point for single-source use (tests, ad-hoc runs)."""
    return asyncio.run(scrape_async(limit=limit, base_url=base_url, queries=queries))
