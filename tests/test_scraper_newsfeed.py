"""Tests for the RSS news source (scraper.newsfeed).

`parse_feed` is pure, so everything here runs offline. The RSS fixture is a
trimmed capture of a real Mining.com.au response — a hand-written one would only
prove the parser handles XML the way the test author imagined it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scraper.newsfeed import (
    MAX_SUMMARY_CHARS,
    Feed,
    _configured_feeds,
    parse_feed,
)

FIXTURE = Path(__file__).parent / "fixtures" / "newsfeed_rss.xml"
AU = Feed("Mining.com.au", "https://mining.com.au/feed/", "AU")
PNG = Feed("Business Advantage PNG", "https://www.businessadvantagepng.com/feed/", "PNG")

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example</title>
  <entry>
    <title>Downer wins $340M Inland Rail contract</title>
    <link rel="replies" href="https://example.com/comments/1"/>
    <link rel="alternate" href="https://example.com/downer-inland-rail"/>
    <published>2026-08-17T09:00:00Z</published>
    <summary>&lt;p&gt;The contractor will deliver &lt;b&gt;track works&lt;/b&gt; through 2028.&lt;/p&gt;</summary>
  </entry>
</feed>
"""


@pytest.fixture
def rss() -> str:
    return FIXTURE.read_text(encoding="utf-8")


# ---------- RSS ----------


def test_parses_articles_from_a_real_feed(rss):
    records = parse_feed(rss, AU)
    assert len(records) == 3
    assert all(r["title"] for r in records)


def test_record_shape_matches_what_ingest_consumes(rss):
    r = parse_feed(rss, AU)[0]
    for key in ("source_url", "raw_content", "captured_at", "source_name",
                "source_type", "geography"):
        assert key in r, f"loader.ingest reads {key}"
    assert r["source_name"] == "newsfeed"


def test_source_type_is_news_not_job_board(rss):
    """The category mix depends on this: a contract award is not a vacancy, and
    the digest's `project` and `financial` buckets exist for exactly this."""
    assert parse_feed(rss, AU)[0]["source_type"] == "news"


def test_geography_comes_from_the_publication(rss):
    """Unlike the job boards, the masthead already says which market this is —
    nothing has to be inferred from the article text."""
    assert {r["geography"] for r in parse_feed(rss, AU)} == {"AU"}
    assert {r["geography"] for r in parse_feed(rss, PNG)} == {"PNG"}


def test_raw_content_names_the_publication(rss):
    """Gemini sees only raw_content, so the source has to be inside it."""
    assert "Mining.com.au" in parse_feed(rss, AU)[0]["raw_content"]


def test_article_url_is_kept_for_dedupe(rss):
    urls = [r["source_url"] for r in parse_feed(rss, AU)]
    assert all(u.startswith("http") for u in urls)
    assert len(set(urls)) == len(urls), "each article needs its own url"


def test_html_is_stripped_from_summaries(rss):
    for r in parse_feed(rss, AU):
        assert "<p>" not in r["raw_content"]
        assert "<" not in r["raw_content"].replace("<", "")


def test_long_summaries_are_truncated(rss):
    for r in parse_feed(rss, AU):
        summary = r["raw_content"].split(" | ", 2)[-1]
        assert len(summary) <= MAX_SUMMARY_CHARS + 2  # +2 for the ellipsis


# ---------- Atom ----------


def test_atom_feeds_parse_too():
    """Some PNG outlets publish Atom. Which format a site emits should not be
    the caller's problem."""
    records = parse_feed(ATOM, AU)
    assert len(records) == 1
    assert records[0]["title"] == "Downer wins $340M Inland Rail contract"


def test_atom_link_prefers_the_article_over_other_rels():
    """Atom entries carry several <link> elements; `rel="replies"` is a comment
    thread, not the article."""
    assert parse_feed(ATOM, AU)[0]["source_url"] == "https://example.com/downer-inland-rail"


def test_atom_summary_html_is_unescaped_and_stripped():
    raw = parse_feed(ATOM, AU)[0]["raw_content"]
    assert "track works" in raw
    assert "<b>" not in raw and "&lt;" not in raw


# ---------- failure modes ----------


def test_malformed_xml_returns_empty_rather_than_raising():
    """One publication serving a broken feed must not take down the run."""
    assert parse_feed("<rss><channel><item>unclosed", AU) == []


def test_empty_feed_returns_empty():
    assert parse_feed('<?xml version="1.0"?><rss><channel/></rss>', AU) == []


def test_entries_without_a_title_are_skipped():
    xml = '<?xml version="1.0"?><rss><channel><item><description>no title</description></item></channel></rss>'
    assert parse_feed(xml, AU) == []


def test_entries_without_a_link_still_get_a_unique_url():
    """Falling back to the feed url alone would collide on the dedupe index and
    leave a single survivor per feed."""
    xml = ('<?xml version="1.0"?><rss><channel>'
           '<item><title>First story</title></item>'
           '<item><title>Second story</title></item>'
           '</channel></rss>')
    urls = [r["source_url"] for r in parse_feed(xml, AU)]
    assert len(set(urls)) == 2


# ---------- configuration ----------


def _with_feeds(monkeypatch, feeds: tuple[str, ...]):
    """Settings is frozen, so swap in a clone rather than assigning to a field."""
    import dataclasses
    import scraper.newsfeed as nf
    monkeypatch.setattr(nf, "settings", dataclasses.replace(nf.settings, news_feeds=feeds))
    return nf


def test_defaults_are_used_when_nothing_is_configured(monkeypatch):
    nf = _with_feeds(monkeypatch, ())
    assert _configured_feeds() == nf.FEEDS


def test_news_feeds_env_overrides_the_defaults(monkeypatch):
    _with_feeds(monkeypatch, ("The National|https://thenational.com.pg/feed|png",))
    feeds = _configured_feeds()
    assert len(feeds) == 1
    assert feeds[0].name == "The National"
    assert feeds[0].geography == "PNG", "geography is normalised to upper case"


def test_malformed_config_entries_are_skipped_not_fatal(monkeypatch):
    _with_feeds(monkeypatch, ("missing-the-other-fields", "Good|https://example.com/feed|AU"))
    feeds = _configured_feeds()
    assert len(feeds) == 1 and feeds[0].name == "Good"


def test_a_wholly_malformed_config_falls_back_to_defaults(monkeypatch):
    """Better a working default than no source at all."""
    nf = _with_feeds(monkeypatch, ("nonsense",))
    assert _configured_feeds() == nf.FEEDS


def test_settings_repr_does_not_leak_secrets():
    """Found the hard way: a failing test in this file printed the live Gemini
    key, because the default dataclass repr includes every field."""
    from config.settings import settings as real
    text = repr(real)
    for name in ("gemini_api_key", "session_secret", "database_url",
                 "google_client_secret", "slack_webhook_url"):
        value = getattr(real, name)
        if value:
            assert str(value) not in text, f"{name} appears in repr(Settings)"
    assert "gemini_model" in text, "non-secret fields should still be visible"
