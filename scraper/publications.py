"""Which publication a signal actually came from.

`source_name` names the collector, not the publisher. That was enough while each
collector read one site, and stopped being enough when `newsfeed` grew to four:
a row labelled "newsfeed" might be Mining.com.au, Australian Mining,
Infrastructure Magazine or Business Advantage PNG, and those are not
interchangeable to a consultant deciding how much weight a story deserves.

Resolved at read time from the article's own address rather than stored at
ingest. The signals table has no column for it, and adding one would label only
rows collected from here on; reading the domain labels every row already in the
database, back to the first collection, with no migration and no backfill.
"""
from __future__ import annotations

from urllib.parse import urlparse

#: Collectors that read a single publication, so the answer does not depend on
#: the URL. Job boards are absent on purpose: for them the collector *is* the
#: publication, and repeating its name underneath would be noise.
SINGLE_PUBLICATION: dict[str, str] = {
    "pngbusinessnews": "PNG Business News",
    "austender": "AusTender",
    "asx": "ASX Announcements",
    "worldbank": "World Bank",
    "ted": "TED",
}


def _host(url: str | None) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _feed_hosts() -> dict[str, str]:
    """Host -> publication name, from the feeds the collector is configured with.

    Read from the live configuration rather than a copy of the feed list, so a
    publication added through NEWS_FEEDS is named correctly without anyone
    remembering to update a second table here.
    """
    try:
        from scraper.newsfeed import _configured_feeds
    except Exception:  # noqa: BLE001 - a label is not worth failing a page
        return {}
    return {_host(f.url): f.name for f in _configured_feeds() if _host(f.url)}


def publication_for(source_name: str | None, source_url: str | None) -> str | None:
    """The publication behind a signal, or None when the collector name already
    says it.

    None rather than a guess for a news URL whose domain matches no configured
    feed — a feed removed from the list still has rows in the database, and an
    unfamiliar domain shown as-is would be better than a wrong publication name,
    but no label at all is better still than either.
    """
    name = (source_name or "").lower()
    if name in SINGLE_PUBLICATION:
        return SINGLE_PUBLICATION[name]
    if name == "newsfeed":
        return _feed_hosts().get(_host(source_url))
    # A catalogued publication is stored under its own id, and its label is its
    # name. Job boards are left out for the reason given above.
    from scraper import catalog

    src = catalog.get(name)
    if src is not None and src.collector == catalog.RSS:
        return src.label
    return None


def source_id_for(source_name: str | None, source_url: str | None) -> str:
    """The catalogue source a stored signal belongs to.

    Every publication used to be collected under the single name "newsfeed".
    Each now has a source of its own, but the rows already in the database keep
    the old name — rewriting them would be a migration of every article ever
    collected, for the sake of a label.

    So the old rows are attributed here instead, by the article's own address:
    a "newsfeed" row from australianmining.com.au counts towards Australian
    Mining. A "newsfeed" row from a domain no catalogued publication uses stays
    "newsfeed", which is where the custom feeds still report.
    """
    name = (source_name or "").lower()
    if name != "newsfeed":
        return name
    from scraper import catalog

    host = _host(source_url)
    if not host:
        return name
    for src in catalog.FEEDS:
        if _host(src.feed_url) == host or _host(src.url) == host:
            return src.id
    return name


#: What a row stored under the old shared name is called when its address
#: matches no catalogued publication, or when there is no address to go by.
LEGACY_NEWSFEED_LABEL = "Industry news (older runs)"


def label_for(source_name: str | None, source_url: str | None = None) -> str:
    """The name a reader sees for a stored source.

    The catalogue's label for whichever source the row belongs to — so an
    article reads "Australian Mining" whether it was stored under the old
    shared name or under its own. A stored name the catalogue does not know (a
    retired scraper) is shown as it is.
    """
    from scraper import catalog

    source_id = source_id_for(source_name, source_url)
    if source_id == "newsfeed":
        from config.settings import settings

        # With custom feeds configured this name is live; without them, every
        # row under it is from before the publications were split out.
        return catalog.label_for("newsfeed") if settings.news_feeds else LEGACY_NEWSFEED_LABEL
    return catalog.label_for(source_id)
