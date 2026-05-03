"""Tests for scraper.pngworkforce. Uses a saved HTML fixture, no live network."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scraper.pngworkforce import parse_listing, scrape

FIXTURE = Path(__file__).parent / "fixtures" / "pngworkforce_listing.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_listing_finds_three_job_cards(html):
    records = parse_listing(html, source_url="https://www.pngworkforce.com/jobs",
                            base_url="https://www.pngworkforce.com")
    assert len(records) == 3


def test_parse_listing_resolves_relative_urls(html):
    records = parse_listing(html, source_url="https://www.pngworkforce.com/jobs",
                            base_url="https://www.pngworkforce.com")
    urls = [r["source_url"] for r in records]
    assert "https://www.pngworkforce.com/jobs/maintenance-planner-ok-tedi" in urls
    assert "https://www.pngworkforce.com/jobs/process-operator-lihir" in urls
    assert "https://www.pngworkforce.com/jobs/hse-advisor-papua-lng" in urls


def test_parse_listing_captures_titles_and_locations(html):
    records = parse_listing(html, source_url="https://www.pngworkforce.com/jobs")
    titles = [r["title"] for r in records]
    assert any("Maintenance Planner" in t for t in titles)
    assert any("Process Operator" in t for t in titles)
    assert any("HSE Advisor" in t for t in titles)
    locations = [r["location"] for r in records if r["location"]]
    assert any("Tabubil" in loc for loc in locations)
    assert any("Lihir" in loc for loc in locations)


def test_parse_listing_raw_content_includes_company_signal(html):
    records = parse_listing(html, source_url="https://www.pngworkforce.com/jobs")
    blob = " || ".join(r["raw_content"] for r in records)
    assert "OTML" in blob or "Ok Tedi" in blob
    assert "Newmont" in blob
    assert "TotalEnergies" in blob
    # The non-job article should NOT contribute
    assert "This is a news block" not in blob


def test_parse_listing_handles_empty_html():
    records = parse_listing("<html><body></body></html>",
                            source_url="https://www.pngworkforce.com/jobs")
    assert records == []


def test_parse_listing_captured_at_is_iso_utc(html):
    records = parse_listing(html, source_url="https://www.pngworkforce.com/jobs")
    for r in records:
        assert r["captured_at"].endswith("+00:00")


def test_scrape_returns_empty_on_invalid_url():
    assert scrape(limit=10, base_url="not-a-url") == []
    assert scrape(limit=10, base_url="") == []


def test_scrape_returns_empty_when_crawler_raises():
    async def _boom(*_a, **_k):
        raise RuntimeError("network down")
    with patch("scraper.pngworkforce._crawl_async", side_effect=_boom):
        assert scrape(limit=10, base_url="https://www.pngworkforce.com") == []
