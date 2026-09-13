"""Naming the publication behind a collector.

"newsfeed" covers four titles. A consultant weighing a story needs to know
whether it is Australian Mining or a general business magazine, and the
collector name cannot say. The label is derived from the article's own address
at read time, so it covers every row already collected without a migration.
"""
from __future__ import annotations

from scraper.publications import publication_for


def test_each_newsfeed_domain_names_its_publication():
    assert publication_for("newsfeed", "https://mining.com.au/story-1") == "Mining.com.au"
    assert publication_for(
        "newsfeed", "https://www.australianmining.com.au/maritana-pushes/") == "Australian Mining"
    assert publication_for(
        "newsfeed", "https://infrastructuremagazine.com.au/m1-extension/") == "Infrastructure Magazine"
    assert publication_for(
        "newsfeed", "https://www.businessadvantagepng.com/png-power/") == "Business Advantage PNG"


def test_www_and_bare_domains_resolve_alike():
    """Feeds are configured with one form and link with the other often enough
    that matching on the literal host would label half the rows."""
    assert publication_for("newsfeed", "https://australianmining.com.au/x") == "Australian Mining"
    assert publication_for("newsfeed", "https://www.mining.com.au/x") == "Mining.com.au"


def test_single_publication_collectors_are_named_whatever_the_url():
    assert publication_for("pngbusinessnews", "https://www.pngbusinessnews.com/articles/2026/9/x") \
        == "PNG Business News"
    assert publication_for("austender", "https://www.tenders.gov.au/Atm/Show/abc") == "AusTender"


def test_job_boards_get_no_label():
    """For a job board the collector is the publication, so repeating it
    underneath would be noise."""
    assert publication_for("seek", "https://au.seek.com/job/1") is None
    assert publication_for("adzuna", "https://www.adzuna.com.au/details/1") is None
    assert publication_for("pngworkforce", "https://www.pngworkforce.com/jobs/1") is None


def test_an_unknown_news_domain_gets_no_label_rather_than_a_guess():
    """A feed removed from the list still has rows in the database. No label is
    better than a wrong one."""
    assert publication_for("newsfeed", "https://some-retired-feed.example/story") is None


def test_missing_inputs_do_not_raise():
    assert publication_for(None, None) is None
    assert publication_for("newsfeed", None) is None
    assert publication_for("newsfeed", "not a url") is None
