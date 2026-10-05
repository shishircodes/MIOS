"""The one default search, and putting it into whatever actor a board uses.

Pinned: the keyword and count fields are read from an actor's own input schema,
so the same search reaches an actor nobody here has seen; a schema that cannot
be read falls back to the common names instead of sending nothing; a board's
own search settings replace the default; and the board's limit reaches the
actor under the actor's own name for it.

The two schemas below are cut down from what Apify published for the actors
named in production on 5 Oct 2026. Nothing here touches the network.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from scraper import apify, apify_search, catalog
from scraper.apify_search import Fields

SEEK_SCHEMA = {"properties": {
    "searchUrl": {"type": "string"},
    "maxResults": {"type": "integer", "default": 300, "maximum": 550},
    "searchTerm": {"type": "string"},
    "sortBy": {"type": "string", "enum": ["KeywordRelevance", "ListedDate"]},
    "construction": {"type": "boolean"},
}}
JORA_SCHEMA = {"properties": {
    "startUrl": {"type": "string"},
    "keyword": {"type": "string"},
    "country": {"type": "string", "default": "Australia"},
    "results_wanted": {"type": "integer", "default": 20, "minimum": 1},
    "max_pages": {"type": "integer", "default": 10},
}}
TOKEN = "apify_api_test_only_0123456789abcdef"
ADMIN = "admin@example.com"


# ---------- reading an actor's schema ----------


def test_the_fields_are_read_from_the_actors_own_schema():
    assert apify_search.fields_from_schema(SEEK_SCHEMA) == Fields(
        keyword="searchTerm", count="maxResults", count_max=550)
    assert apify_search.fields_from_schema(JORA_SCHEMA) == Fields(
        keyword="keyword", count="results_wanted")


def test_names_are_matched_however_they_are_written():
    schema = {"properties": {"Search_Query": {"type": "string"}, "max-jobs": {"type": "integer"}}}
    assert apify_search.fields_from_schema(schema) == Fields(keyword="Search_Query", count="max-jobs")


def test_a_field_of_the_wrong_kind_is_not_used():
    """A switch called `keyword`, or a count that is text, is not what it looks like."""
    schema = {"properties": {"keyword": {"type": "boolean"}, "query": {"type": "string"},
                             "limit": {"type": "string"}}}
    assert apify_search.fields_from_schema(schema) == Fields(keyword="query")


def test_an_actor_that_takes_only_addresses_has_no_keyword_field():
    fields = apify_search.fields_from_schema({"properties": {"startUrls": {"type": "array"}}})
    assert fields.known and fields.keyword is None
    assert apify_search.search_input(fields, "mining") == {}


@pytest.mark.parametrize("schema", [None, {}, {"properties": {}}, "not a schema"])
def test_no_schema_is_unknown_rather_than_empty(schema):
    assert apify_search.fields_from_schema(schema).known is False


def _published(schema, *, as_text=True):
    body = {"data": {"inputSchema": json.dumps(schema) if as_text else schema}}
    return MagicMock(status_code=200, json=lambda: body)


def test_the_schema_is_fetched_from_apify(monkeypatch):
    seen = {}

    def get(url, **kw):
        seen.update(url=url, **kw)
        return _published(SEEK_SCHEMA)

    monkeypatch.setattr(apify_search, "_http_get", get)
    fields = apify_search.fetch_fields("websift/seek-job-scraper", TOKEN)
    assert fields.keyword == "searchTerm" and fields.count_max == 550
    assert "websift~seek-job-scraper" in seen["url"] and TOKEN not in seen["url"]
    assert seen["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_a_failed_lookup_is_unknown_and_never_raises(monkeypatch):
    monkeypatch.setattr(apify_search, "_http_get", lambda *a, **k: MagicMock(status_code=404))
    assert apify_search.fetch_fields("someone/gone").known is False

    def boom(*_a, **_k):
        raise TimeoutError("slow")

    monkeypatch.setattr(apify_search, "_http_get", boom)
    assert apify_search.fetch_fields("someone/slow").known is False


# ---------- the search, in the actor's words ----------


def test_the_keywords_go_where_the_actor_reads_them():
    search = "mining OR oil and gas OR construction"
    assert apify_search.search_input(Fields(keyword="searchTerm"), search) == {"searchTerm": search}
    assert apify_search.search_input(Fields(keyword="keywords", keyword_is_list=True), search) == {
        "keywords": ["mining", "oil and gas", "construction"]}


def test_an_unreadable_schema_falls_back_to_the_common_names():
    """Better the keywords under five names, four of them ignored, than an
    actor left to return every kind of job."""
    sent = apify_search.search_input(apify_search.UNKNOWN, "mining")
    assert sent["keyword"] == sent["searchTerm"] == sent["query"] == "mining"
    assert all(isinstance(v, str) for v in sent.values()), "strings only, so nothing is rejected"


def test_the_limit_goes_under_the_actors_own_name_too():
    seek = Fields(keyword="searchTerm", count="maxResults", count_max=550)
    assert apify_search.with_limit({}, seek, 50) == {"maxItems": 50, "maxResults": 50}
    assert apify_search.with_limit({}, seek, 900)["maxResults"] == 550, "the actor accepts no more"
    assert apify_search.with_limit({"maxResults": 300}, seek, 50)["maxResults"] == 50, \
        "the board's limit wins over what was typed"
    assert apify_search.with_limit({}, apify_search.UNKNOWN, 30) == {"maxItems": 30}


def test_the_built_in_search_is_easy_skills_sectors():
    assert apify_search.terms(apify_search.DEFAULT_KEYWORDS) == [
        "mining", "oil and gas", "energy", "construction", "defence"]


# ---------- end to end, with Apify replaced ----------


def _run(monkeypatch, schemas):
    """Run a board's actor with Apify faked; return what was posted to it."""
    posted = {}

    def get(url, **_kw):
        for actor, schema in schemas.items():
            if actor.replace("/", "~") in url:
                return _published(schema)
        return MagicMock(status_code=404)

    class _Res:
        def raise_for_status(self): pass
        def json(self): return [{"title": "Driller", "url": "https://example.com/job/1"}]

    def post(url, **kw):
        posted.update(url=url, **kw)
        return _Res()

    monkeypatch.setattr(apify_search, "_http_get", get)
    monkeypatch.setattr(apify.requests, "post", post)
    return posted


def test_every_board_gets_the_same_default_search(panel, monkeypatch):
    """The production run of 5 Oct 2026 had no search at all on either board."""
    panel.set_apify_token(TOKEN, changed_by=ADMIN)
    panel.set_board("seek", "websift/seek-job-scraper", "", changed_by=ADMIN)
    panel.set_board("jora", "shahidirfan/Jora-Jobs-Scraper", "", changed_by=ADMIN)
    posted = _run(monkeypatch, {"websift/seek-job-scraper": SEEK_SCHEMA,
                                "shahidirfan/Jora-Jobs-Scraper": JORA_SCHEMA})

    asyncio.run(apify.scrape_async(catalog.get("seek"), limit=50))
    assert posted["json"] == {"searchTerm": apify_search.DEFAULT_KEYWORDS,
                              "maxItems": 50, "maxResults": 50}

    asyncio.run(apify.scrape_async(catalog.get("jora"), limit=30))
    assert posted["json"] == {"keyword": apify_search.DEFAULT_KEYWORDS,
                              "maxItems": 30, "results_wanted": 30}


def test_changing_the_default_search_changes_it_for_every_board(panel, monkeypatch):
    panel.set_apify_token(TOKEN, changed_by=ADMIN)
    panel.set_board("seek", "websift/seek-job-scraper", "", changed_by=ADMIN)
    panel.set_board("jora", "shahidirfan/Jora-Jobs-Scraper", "", changed_by=ADMIN)
    panel.set_apify_search("drill and blast OR shotfirer", changed_by=ADMIN)
    posted = _run(monkeypatch, {"websift/seek-job-scraper": SEEK_SCHEMA,
                                "shahidirfan/Jora-Jobs-Scraper": JORA_SCHEMA})

    asyncio.run(apify.scrape_async(catalog.get("seek"), limit=50))
    assert posted["json"]["searchTerm"] == "drill and blast OR shotfirer"
    asyncio.run(apify.scrape_async(catalog.get("jora"), limit=30))
    assert posted["json"]["keyword"] == "drill and blast OR shotfirer"


def test_a_boards_own_search_replaces_the_default(panel, monkeypatch):
    panel.set_apify_token(TOKEN, changed_by=ADMIN)
    panel.set_board("seek", "websift/seek-job-scraper",
                    '{"construction": true, "sortBy": "ListedDate"}', changed_by=ADMIN)
    posted = _run(monkeypatch, {"websift/seek-job-scraper": SEEK_SCHEMA})

    asyncio.run(apify.scrape_async(catalog.get("seek"), limit=50))
    assert posted["json"] == {"construction": True, "sortBy": "ListedDate",
                              "maxItems": 50, "maxResults": 50}, "no keywords added to it"


def test_an_actor_whose_schema_cannot_be_read_still_gets_the_search(panel, monkeypatch):
    panel.set_apify_token(TOKEN, changed_by=ADMIN)
    panel.set_board("indeed", "someone/private-indeed", "", changed_by=ADMIN)
    posted = _run(monkeypatch, {})

    asyncio.run(apify.scrape_async(catalog.get("indeed"), limit=30))
    assert posted["json"]["keyword"] == apify_search.DEFAULT_KEYWORDS
    assert posted["json"]["maxItems"] == 30
