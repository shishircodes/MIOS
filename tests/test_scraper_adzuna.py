"""Tests for scraper.adzuna. Uses a saved JSON fixture, no live network."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from config.settings import settings as real_settings
from scraper.adzuna import parse_results, scrape, watchlist_queries

FIXTURE = Path(__file__).parent / "fixtures" / "adzuna_search.json"


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def records(payload) -> list[dict]:
    return parse_results(payload)


# ---------- parsing ----------


def test_parses_every_job(records):
    assert len(records) == 3


def test_captures_company_location_and_title(records):
    assert [r["title"] for r in records][:2] == ["Maintenance Planner", "Process Operator"]
    assert [r["company"] for r in records][:2] == ["BHP", "Rio Tinto"]
    assert "Newman" in records[0]["location"]


def test_tracking_params_are_stripped_for_stable_dedupe(records):
    """redirect_url carries per-request utm/v params, so the same job fetched
    twice would look like two rows unless the query is dropped."""
    assert records[0]["source_url"] == "https://www.adzuna.com.au/details/5182749311"
    assert all("?" not in r["source_url"] for r in records)


def test_predicted_salary_is_labelled(records):
    """salary_is_predicted=1 means Adzuna inferred the range rather than the ad
    stating it — the classifier must not read it as a quoted figure."""
    assert "(estimated)" not in records[0]["raw_content"]      # salary_is_predicted "0"
    assert "(estimated)" in records[1]["raw_content"]          # salary_is_predicted "1"


def test_sparse_records_still_parse(records):
    """Adzuna omits keys rather than sending nulls; a partial job is still a signal."""
    sparse = records[2]
    assert sparse["company"] is None
    assert sparse["salary"] is None
    assert sparse["title"] == "Site Superintendent"
    assert sparse["raw_content"]


def test_posted_date_is_preserved(records):
    """Unlike the HTML scrapers, Adzuna reports when the ad went live — worth
    keeping separately from when we happened to look."""
    assert records[0]["posted"] == "2026-08-08T04:12:33Z"
    assert records[0]["captured_at"].endswith("+00:00")


def test_tags_source_and_geography(records):
    assert {r["source_name"] for r in records} == {"adzuna"}
    assert {r["geography"] for r in records} == {"AU"}


def test_jobs_without_a_title_are_skipped():
    assert parse_results({"results": [{"id": "1", "description": "no title"}]}) == []


def test_malformed_response_returns_empty():
    assert parse_results({}) == []
    assert parse_results({"results": "not-a-list"}) == []
    assert parse_results({"results": [None, "junk"]}) == []


# ---------- queries ----------


def test_queries_default_to_the_watchlist():
    """Searching per company is the point of this source — it turns the watchlist
    into targeted queries rather than hoping a category browse surfaces them."""
    q = watchlist_queries()
    assert "BHP" in q and "Rio Tinto" in q
    assert len(q) == 20


# ---------- graceful failure ----------


def test_returns_empty_without_credentials(monkeypatch):
    patched = dataclasses.replace(real_settings, adzuna_app_id="", adzuna_app_key="")
    monkeypatch.setattr("scraper.adzuna.settings", patched)
    assert scrape(limit=10) == []


def test_one_failing_query_does_not_lose_the_others(monkeypatch, payload):
    """A single bad query must not discard results already gathered."""
    patched = dataclasses.replace(real_settings, adzuna_app_id="id", adzuna_app_key="key")
    monkeypatch.setattr("scraper.adzuna.settings", patched)

    calls: list[str] = []

    def _fake(query, per_page):
        calls.append(query)
        if query == "Rio Tinto":
            raise RuntimeError("502 from Adzuna")
        return parse_results(payload)

    with patch("scraper.adzuna._search_once", side_effect=_fake):
        out = scrape(limit=50, queries=["BHP", "Rio Tinto", "Newmont"])

    assert calls == ["BHP", "Rio Tinto", "Newmont"]
    assert len(out) == 3, "results from the working queries must survive"


def test_duplicate_jobs_across_queries_are_deduped(monkeypatch, payload):
    patched = dataclasses.replace(real_settings, adzuna_app_id="id", adzuna_app_key="key")
    monkeypatch.setattr("scraper.adzuna.settings", patched)

    with patch("scraper.adzuna._search_once", return_value=parse_results(payload)):
        out = scrape(limit=50, queries=["BHP", "Rio Tinto"])

    # Same three jobs returned for both queries; only three rows survive.
    assert len(out) == 3
    assert len({r["source_url"] for r in out}) == 3


def test_limit_is_respected(monkeypatch, payload):
    patched = dataclasses.replace(real_settings, adzuna_app_id="id", adzuna_app_key="key")
    monkeypatch.setattr("scraper.adzuna.settings", patched)

    with patch("scraper.adzuna._search_once", return_value=parse_results(payload)):
        assert len(scrape(limit=2, queries=["BHP"])) == 2


def test_network_failure_returns_empty(monkeypatch):
    patched = dataclasses.replace(real_settings, adzuna_app_id="id", adzuna_app_key="key")
    monkeypatch.setattr("scraper.adzuna.settings", patched)

    with patch("scraper.adzuna._search_once", side_effect=RuntimeError("network down")):
        assert scrape(limit=10, queries=["BHP"]) == []
