"""Scraper registry.

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
    # Imported lazily so `import scraper` doesn't drag in bs4/crawlee.
    from scraper import adzuna, pngworkforce, seek

    return {
        "pngworkforce": pngworkforce.scrape_async,
        "seek": seek.scrape_async,
        "adzuna": adzuna.scrape_async,
    }


SOURCE_NAMES: tuple[str, ...] = ("pngworkforce", "seek", "adzuna")


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
) -> list[dict[str, Any]]:
    """Scrape the named sources sequentially in the caller's event loop."""
    registry = _registry()
    out: list[dict[str, Any]] = []
    for name in _resolve(sources, base_url):
        records = await registry[name](limit=limit, base_url=base_url)
        log.info("scrape_all: %s returned %d records", name, len(records))
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
) -> list[dict[str, Any]]:
    """Scrape the named sources (default: all) and return the merged records.

    `limit` is the per-source cap, not a global one — a total budget would let
    whichever source runs first starve the others.

    `base_url` overrides the target for the selected source; it's rejected when
    more than one source is selected, since a single URL can't apply to both.
    """
    _resolve(sources, base_url)  # fail fast on bad args, before spinning a loop
    return asyncio.run(scrape_all_async(limit=limit, sources=sources, base_url=base_url))
