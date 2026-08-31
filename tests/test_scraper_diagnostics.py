"""Tests for why a crawl came back empty (scraper._diagnostics).

These exist because of a real production incident: pngworkforce and seek
collected nothing from the deployed host while working from a developer's
machine, and the only evidence either way was one INFO line reading
"returned 0 records". Hours went into guessing between "we were blocked" and
"the markup changed" — two problems with completely different fixes.

So what is pinned here is not formatting. It is that the three outcomes are
*distinguishable from the log alone*.
"""
from __future__ import annotations

import logging

import pytest

from scraper._diagnostics import MAX_RECORDED_ERRORS, CrawlWatch


class _Stats:
    """Stands in for crawlee's FinalStatistics."""

    def __init__(self, finished: int = 0, failed: int = 0):
        self.requests_finished = finished
        self.requests_failed = failed


def test_being_refused_is_reported_as_an_error(caplog):
    """The case that matters: the source is up, and it will not serve us. This
    must not read like an empty listing."""
    watch = CrawlWatch("seek")
    watch.errors.append("HTTP 403 on https://au.seek.com/x: blocked")

    with caplog.at_level(logging.DEBUG):
        watch.report(_Stats(finished=0, failed=3), collected=0)

    assert any(r.levelno == logging.ERROR for r in caplog.records)
    text = caplog.text
    assert "403" in text, "the status code is what identifies a block"
    assert "not an empty listing" in text


def test_a_page_that_parses_to_nothing_is_a_different_message(caplog):
    """Fetched fine, parsed nothing — the markup changed. Reporting this as a
    block would send someone hunting for a firewall that does not exist."""
    watch = CrawlWatch("pngworkforce")

    with caplog.at_level(logging.DEBUG):
        watch.report(_Stats(finished=4, failed=0), collected=0)

    assert "markup changed" in caplog.text
    assert "refusing us" not in caplog.text


def test_the_two_empty_outcomes_do_not_share_wording(caplog):
    """The whole point. If both said "returned 0 records" we would be back to
    the incident this module was written for."""
    blocked, parsed_nothing = CrawlWatch("a"), CrawlWatch("b")

    with caplog.at_level(logging.DEBUG):
        blocked.report(_Stats(finished=0, failed=2), collected=0)
        first = caplog.text
        caplog.clear()
        parsed_nothing.report(_Stats(finished=2, failed=0), collected=0)
        second = caplog.text

    assert first != second
    assert first.strip() and second.strip()


def test_a_partial_collection_says_the_count_is_incomplete(caplog):
    """Records came back, but some pages were lost, so the number is a floor.
    Silence here would let a truncated scrape look like a full one."""
    watch = CrawlWatch("seek")
    watch.errors.append("HTTP 429 on https://au.seek.com/y: rate limited")

    with caplog.at_level(logging.DEBUG):
        watch.report(_Stats(finished=3, failed=1), collected=12)

    assert "incomplete" in caplog.text
    assert "429" in caplog.text


def test_a_clean_run_says_nothing(caplog):
    """Diagnostics that fire on success get ignored on failure."""
    with caplog.at_level(logging.DEBUG):
        CrawlWatch("seek").report(_Stats(finished=4, failed=0), collected=40)

    assert caplog.text == ""


def test_no_requests_at_all_is_reported(caplog):
    with caplog.at_level(logging.DEBUG):
        CrawlWatch("seek").report(_Stats(finished=0, failed=0), collected=0)

    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_only_a_few_errors_are_kept():
    """A crawl retrying twenty pages must not put twenty lines in the log; the
    first few identify the cause and the rest are the same thing again."""
    import asyncio

    watch = CrawlWatch("seek")

    class _FakeCrawler:
        def failed_request_handler(self, fn):
            self.fn = fn
            return fn

    crawler = _FakeCrawler()
    watch.attach(crawler)

    class _Ctx:
        class request:
            url = "https://au.seek.com/page"

    async def _drive():
        for _ in range(10):
            await crawler.fn(_Ctx(), RuntimeError("blocked"))

    asyncio.run(_drive())
    assert len(watch.errors) == MAX_RECORDED_ERRORS


def test_the_recorded_error_names_the_url_and_cause():
    import asyncio

    watch = CrawlWatch("seek")

    class _FakeCrawler:
        def failed_request_handler(self, fn):
            self.fn = fn
            return fn

    crawler = _FakeCrawler()
    watch.attach(crawler)

    class _Ctx:
        class request:
            url = "https://au.seek.com/jobs"

    asyncio.run(crawler.fn(_Ctx(), RuntimeError("connection refused")))

    assert "au.seek.com/jobs" in watch.errors[0]
    assert "connection refused" in watch.errors[0]
