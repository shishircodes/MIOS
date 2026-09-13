"""PNG Business News, and the way this site fails.

The publication serves no feed, so this reads its category listings. Two things
about the site shape the module and both are pinned here:

* article links are **absolute**, so the obvious relative-path match finds
  nothing at all;
* an unrecognised category returns **the home page, with status 200**, so a
  renamed category would be scraped forever and look healthy doing it — the
  same silent failure that had PNGworkforce collecting the wrong page for weeks.
"""
from __future__ import annotations

from scraper.pngbusinessnews import (
    CATEGORIES,
    MIN_TITLE_CHARS,
    _looks_like_category,
    parse_listing,
)

LISTING = """
<html><body>
  <h3>Category:</h3>
  <div class="card">
    <h3><a href="https://www.pngbusinessnews.com/articles/2026/9/ok-tedi-declares-k450-million-interim-dividend">
      Ok Tedi Declares K450 Million Interim Dividend as Operations Continue</a></h3>
  </div>
  <div class="card">
    <h3><a href="/articles/2026/8/k92-mining-posts-us-84-6m-q2-profit">
      K92 Mining posts US$84.6m Q2 profit as PNG operations hit production</a></h3>
  </div>
  <div class="card">
    <h3><a href="https://www.pngbusinessnews.com/advertising/media-kit">Media Kit</a></h3>
  </div>
  <div class="card">
    <h3><a href="https://www.pngbusinessnews.com/articles/2026/9/short">Too short</a></h3>
  </div>
</body></html>
"""


# ---------- reading a listing ----------


def test_articles_are_found_whether_the_link_is_absolute_or_relative():
    """The site writes absolute hrefs, which is why a relative-path match found
    zero articles on the first attempt."""
    records = parse_listing(LISTING, "mining")

    urls = [r["source_url"] for r in records]
    assert "https://www.pngbusinessnews.com/articles/2026/9/ok-tedi-declares-k450-million-interim-dividend" in urls
    assert "https://www.pngbusinessnews.com/articles/2026/8/k92-mining-posts-us-84-6m-q2-profit" in urls


def test_non_article_links_are_ignored():
    """Only `/articles/<year>/<month>/<slug>` is an article. The media kit is a
    heading with a link in it like any other."""
    titles = [r["title"] for r in parse_listing(LISTING, "mining")]

    assert not any("Media Kit" in t for t in titles)


def test_a_heading_too_short_to_be_a_headline_is_skipped():
    titles = [r["title"] for r in parse_listing(LISTING, "mining")]

    assert all(len(t) >= MIN_TITLE_CHARS for t in titles)
    assert "Too short" not in titles


def test_the_date_comes_from_the_url_and_claims_only_the_month():
    """The listing markup carries no date element — there is not a single
    `<time>` on the page — so the month in the path is all there is. It is
    reported as a month rather than padded to a day nobody measured."""
    by_url = {r["source_url"]: r for r in parse_listing(LISTING, "mining")}
    rec = by_url["https://www.pngbusinessnews.com/articles/2026/8/k92-mining-posts-us-84-6m-q2-profit"]

    assert rec["posted"] == "2026-08"


def test_the_category_travels_with_the_signal():
    """"Filed under mining" is what the publication itself thinks the story is
    about, which is worth handing to the classifier."""
    rec = parse_listing(LISTING, "oil-and-gas")[0]

    assert "oil and gas" in rec["raw_content"]
    assert "PNG Business News" in rec["raw_content"]


def test_every_record_carries_the_source_and_geography():
    for rec in parse_listing(LISTING, "mining"):
        assert rec["source_name"] == "pngbusinessnews"
        assert rec["geography"] == "PNG"
        assert rec["source_type"] == "news"


def test_the_same_article_twice_is_collected_once():
    doubled = LISTING + LISTING

    records = parse_listing(doubled, "mining")

    assert len({r["source_url"] for r in records}) == len(records)


def test_markup_with_no_articles_yields_nothing_rather_than_failing():
    assert parse_listing("<html><body><p>Nothing here</p></body></html>", "mining") == []


# ---------- the failure that hides ----------


def test_a_page_identical_to_the_home_page_is_refused():
    """An unrecognised category returns the home page with status 200. Without
    this check the scraper would collect the home page under four different
    category names and report a healthy run."""
    home = "<html><body>the home page</body></html>"

    assert _looks_like_category(home, home) is False


def test_a_real_listing_is_accepted():
    home = "<html><body>the home page</body></html>"

    assert _looks_like_category(LISTING, home) is True


def test_without_a_home_page_to_compare_against_pages_are_accepted():
    """If the home page could not be fetched the guard cannot run. Refusing
    everything would turn one failed request into a dead source; accepting is
    the behaviour without the guard at all."""
    assert _looks_like_category(LISTING, None) is True


def test_the_oil_category_is_the_one_that_exists():
    """`/articles/oil-gas` is a real-looking URL that silently serves the home
    page. The site's own is `oil-and-gas`, and getting this wrong is invisible
    without the guard above."""
    assert "oil-and-gas" in CATEGORIES
    assert "oil-gas" not in CATEGORIES
