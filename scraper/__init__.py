"""Scraper registry.

What exists is decided by `scraper/catalog.py`, the one list of every source
in the data-sources guide; this turns the collectable ones into callables.

Each source module exposes an async `scrape_async(limit, base_url=None)` that
never raises — on any failure it logs and returns []. `scrape_all` fans out over
the registered sources so the pipeline stays source-agnostic; one dead source
degrades the run rather than killing it.

Sources are awaited inside a *single* event loop rather than each calling
`asyncio.run`. crawlee binds global state (its storage-client lock) to the loop
that first touches it, so a second `asyncio.run` in the same process dies with
"is bound to a different event loop" — which is exactly the multi-source case.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Protocol

log = logging.getLogger(__name__)


class ScrapeFn(Protocol):
    def __call__(
        self, limit: int = ..., base_url: str | None = ...
    ) -> Awaitable[list[dict[str, Any]]]: ...


def _registry() -> dict[str, ScrapeFn]:
    """Every collectable source in the catalogue, by id.

    Built from `scraper.catalog` rather than listed by hand: an RSS publication
    is read by the shared feed reader, an Apify board by the shared actor
    runner, and everything else by the module named after it.
    """
    # Imported lazily so `import scraper` doesn't drag in bs4/crawlee.
    from functools import partial

    from scraper import (
        adzuna, apify, asx, austender, catalog, miningpeople, newsfeed,
        pngbusinessnews, pngworkforce, ted, worldbank,
    )

    modules = {
        "pngworkforce": pngworkforce, "adzuna": adzuna,
        "newsfeed": newsfeed, "pngbusinessnews": pngbusinessnews,
        "austender": austender, "asx": asx, "worldbank": worldbank, "ted": ted,
        "miningpeople": miningpeople,
    }
    feeds = {f.source: f for f in newsfeed.FEEDS}

    registry: dict[str, ScrapeFn] = {}
    for src in catalog.COLLECTED:
        if src.collector == catalog.RSS:
            registry[src.id] = partial(newsfeed.scrape_feed_async, feeds[src.id])
        elif src.collector == catalog.APIFY:
            registry[src.id] = partial(apify.scrape_async, src)
        else:
            registry[src.id] = modules[src.id].scrape_async
    return registry


def _source_names() -> tuple[str, ...]:
    from scraper import catalog

    return tuple(src.id for src in catalog.COLLECTED)


#: Every source MIOS can collect from, in catalogue order.
SOURCE_NAMES: tuple[str, ...] = _source_names()


def _resolve(sources: list[str] | None, base_url: str | None) -> list[str]:
    names = sources or list(SOURCE_NAMES)
    unknown = [n for n in names if n not in SOURCE_NAMES]
    if unknown:
        raise ValueError(f"unknown source(s): {', '.join(unknown)}; known: {', '.join(SOURCE_NAMES)}")
    if base_url and len(names) > 1:
        raise ValueError("base_url override requires exactly one source")
    return names


async def scrape_all_async(
    limit: int = 200,
    sources: list[str] | None = None,
    base_url: str | None = None,
    limits: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Scrape the named sources sequentially in the caller's event loop.

    `limits` gives each source its own cap; a source missing from it uses
    `limit`.
    """
    registry = _registry()
    out: list[dict[str, Any]] = []
    for name in _resolve(sources, base_url):
        cap = (limits or {}).get(name, limit)
        records = await registry[name](limit=cap, base_url=base_url)
        if records:
            log.info("scrape_all: %s returned %d records", name, len(records))
        else:
            # A source that was asked to collect and returned nothing is a
            # problem, not a routine outcome. The source itself has already
            # logged why; this makes the run summary say that it happened.
            log.warning("scrape_all: %s returned NO records — see the reason logged above",
                        name)
        for r in records:
            # Sources may set their own; default to the registry key so ingest
            # and the digest can attribute every row.
            r.setdefault("source_name", name)
            out.append(r)
    return out


def scrape_all(
    limit: int = 200,
    sources: list[str] | None = None,
    base_url: str | None = None,
    limits: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Scrape the named sources (default: all) and return the merged records.

    `limit` is the per-source cap, not a global one — a total budget would let
    whichever source runs first starve the others.

    `base_url` overrides the target for the selected source; it's rejected when
    more than one source is selected, since a single URL can't apply to both.
    """
    _resolve(sources, base_url)  # fail fast on bad args, before spinning a loop
    return asyncio.run(scrape_all_async(limit=limit, sources=sources, base_url=base_url,
                                        limits=limits))
