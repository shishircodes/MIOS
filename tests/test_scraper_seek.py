"""Tests for scraper.seek. Uses a saved HTML fixture, no live network."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scraper.seek import RobotsDisallowed, _assert_allowed, parse_listing, scrape

FIXTURE = Path(__file__).parent / "fixtures" / "seek_listing.html"
BASE = "https://au.seek.com"
LISTING = f"{BASE}/jobs-in-mining-resources-energy"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def records(html) -> list[dict]:
    return parse_listing(html, source_url=LISTING, base_url=BASE)


def test_parse_listing_finds_three_job_cards(records):
    assert len(records) == 3


def test_parse_listing_ignores_non_job_articles(records):
    blob = " || ".join(r["raw_content"] for r in records)
    assert "promo block" not in blob


def test_parse_listing_captures_title_company_location(records):
    titles = [r["title"] for r in records]
    assert any("Terminal Operator" in t for t in titles)
    companies = [r["company"] for r in records]
    assert "BHP" in companies
    assert any("Townsville" in r["location"] for r in records)


def test_parse_listing_canonicalises_job_urls(records):
    # Tracking params (?type=promoted&ref=...) must be stripped so the same job
    # promoted on one page and organic on another dedupes to one row.
    urls = [r["source_url"] for r in records]
    assert "https://au.seek.com/job/93656065" in urls
    assert all("?" not in u for u in urls)


def test_parse_listing_raw_content_includes_salary_and_classification(records):
    puma = next(r for r in records if "Puma" in (r["company"] or ""))
    assert "$48.66" in puma["raw_content"]
    assert "Oil & Gas - Operations" in puma["raw_content"]
    assert puma["salary"].startswith("$48.66")


def test_parse_listing_tolerates_missing_salary(records):
    bhp = next(r for r in records if r["company"] == "BHP")
    assert bhp["salary"] is None
    assert bhp["raw_content"]


def test_parse_listing_tags_source_and_geography(records):
    assert {r["source_name"] for r in records} == {"seek"}
    assert {r["geography"] for r in records} == {"AU"}


def test_parse_listing_captured_at_is_iso_utc(records):
    for r in records:
        assert r["captured_at"].endswith("+00:00")


def test_parse_listing_handles_empty_html():
    assert parse_listing("<html><body></body></html>", source_url=LISTING) == []


# ---------- robots.txt guard ----------


def test_assert_allowed_accepts_category_paths():
    _assert_allowed(f"{BASE}/jobs-in-mining-resources-energy")
    _assert_allowed(f"{BASE}/jobs-in-mining-resources-energy/mining-operations")


@pytest.mark.parametrize("url", [
    f"{BASE}/jobs-in-mining-resources-energy?page=2",   # Disallow: *?
    f"{BASE}/job/93656065",                             # Disallow: */job/
    f"{BASE}/graphql",
    f"{BASE}/api/jobsearch/v5/search",
])
def test_assert_allowed_rejects_disallowed_paths(url):
    with pytest.raises(RobotsDisallowed):
        _assert_allowed(url)


def test_scrape_skips_disallowed_paths_and_returns_empty():
    # Every configured path is disallowed => nothing left to crawl, no network.
    with patch("scraper.seek._crawl_async") as crawl:
        assert scrape(limit=10, base_url=BASE, paths=["/jobs-in-engineering?page=2"]) == []
        crawl.assert_not_called()


def test_scrape_only_crawls_allowed_targets():
    captured = {}

    async def _fake(targets, base_url, limit):
        captured["targets"] = targets
        return []

    with patch("scraper.seek._crawl_async", side_effect=_fake):
        scrape(limit=10, base_url=BASE, paths=["/jobs-in-engineering", "/job/123"])
    assert captured["targets"] == [f"{BASE}/jobs-in-engineering"]


# ---------- graceful failure ----------


def test_scrape_returns_empty_on_invalid_url():
    assert scrape(limit=10, base_url="not-a-url") == []
    assert scrape(limit=10, base_url="") == []


def test_scrape_returns_empty_when_crawler_raises():
    async def _boom(*_a, **_k):
        raise RuntimeError("network down")
    with patch("scraper.seek._crawl_async", side_effect=_boom):
        assert scrape(limit=10, base_url=BASE) == []
