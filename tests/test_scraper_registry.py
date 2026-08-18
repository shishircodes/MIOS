"""Tests for the multi-source scraper registry (scraper.scrape_all)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import scraper
from scraper import SOURCE_NAMES, scrape_all


def _fake_registry(png=None, seek=None, adzuna=None, news=None):
    """A stand-in registry covering every declared source.

    It must stay in step with SOURCE_NAMES: scrape_all() defaults to all of them,
    so a source missing here fails with a bare KeyError rather than a useful
    message. `test_registry_covers_declared_source_names` guards the real one.
    """
    def _fixed(rows):
        async def _fn(limit=200, base_url=None):
            return list(rows or [])
        return _fn

    return {
        "pngworkforce": _fixed(png),
        "seek": _fixed(seek),
        "adzuna": _fixed(adzuna),
        "newsfeed": _fixed(news),
    }


PNG_REC = {"source_url": "https://www.pngworkforce.com/jobs/view/1", "raw_content": "png job"}
SEEK_REC = {"source_url": "https://au.seek.com/job/1", "raw_content": "seek job", "source_name": "seek"}
ADZUNA_REC = {"source_url": "https://www.adzuna.com.au/details/1", "raw_content": "adzuna job",
              "source_name": "adzuna"}
NEWS_REC = {"source_url": "https://mining.com.au/story-1", "raw_content": "contract award",
            "source_name": "newsfeed", "source_type": "news"}


def test_scrape_all_merges_every_source_by_default():
    with patch.object(scraper, "_registry",
                      lambda: _fake_registry([PNG_REC], [SEEK_REC], [ADZUNA_REC], [NEWS_REC])):
        records = scrape_all(limit=10)
    assert len(records) == 4
    assert {r["source_name"] for r in records} == {"pngworkforce", "seek", "adzuna", "newsfeed"}


def test_scrape_all_can_select_one_source():
    with patch.object(scraper, "_registry", lambda: _fake_registry([PNG_REC], [SEEK_REC])):
        records = scrape_all(limit=10, sources=["seek"])
    assert [r["source_name"] for r in records] == ["seek"]


def test_scrape_all_stamps_source_name_when_scraper_omits_it():
    with patch.object(scraper, "_registry", lambda: _fake_registry([PNG_REC], [])):
        records = scrape_all(limit=10, sources=["pngworkforce"])
    assert records[0]["source_name"] == "pngworkforce"


def test_scrape_all_survives_a_dead_source():
    # A source that fails returns [] (its own contract) — the other still lands.
    with patch.object(scraper, "_registry", lambda: _fake_registry([], [SEEK_REC], [])):
        records = scrape_all(limit=10)
    assert len(records) == 1


def test_scrape_all_rejects_unknown_source():
    with pytest.raises(ValueError, match="unknown source"):
        scrape_all(limit=10, sources=["linkedin"])


def test_scrape_all_rejects_base_url_with_multiple_sources():
    with pytest.raises(ValueError, match="exactly one source"):
        scrape_all(limit=10, base_url="https://example.com")


def test_scrape_all_runs_every_source_in_one_event_loop():
    # Regression: crawlee binds its storage lock to the first event loop it sees,
    # so per-source asyncio.run() calls made the second source fail with
    # "is bound to a different event loop". All sources must share one loop.
    loops = []

    def _registry_capturing_loop():
        async def _fn(limit=200, base_url=None):
            loops.append(id(asyncio.get_running_loop()))
            return []
        return {name: _fn for name in SOURCE_NAMES}

    with patch.object(scraper, "_registry", _registry_capturing_loop):
        scrape_all(limit=10)
    assert len(loops) == len(SOURCE_NAMES)
    assert len(set(loops)) == 1, "every source must share one event loop"


def test_registry_covers_declared_source_names():
    assert set(scraper._registry()) == set(SOURCE_NAMES)
