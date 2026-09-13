"""Industry news via RSS — the lowest-effort source in the data-sources guide,
and the one that fills the biggest hole in what MIOS can currently see.

Everything else on that list is either an HTML scrape (selectors that break on a
redesign, terms-of-use tension, robots.txt carve-outs) or a paid subscription.
RSS is neither: it is a published, stable, machine-readable format that exists
to be polled, needs no key, and is parsed here with the standard library.

It is also a *different kind* of signal. All three existing sources are job
boards, so `hiring_velocity` dominates and categories like `project`,
`financial` and `competitive` are nearly empty — a contract award is news, not a
vacancy. "Downer wins $340M rail contract" can only arrive this way.

One module serves every feed rather than a file per publication, so adding the
rest of the list later is a line in `FEEDS` or an entry in `NEWS_FEEDS`.

Both RSS 2.0 (`<item>`) and Atom (`<entry>`) are handled: the AU mining titles
publish RSS, some PNG outlets publish Atom, and which one a site emits is not
worth caring about at the call site.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

import requests

from config.settings import settings

log = logging.getLogger(__name__)

SOURCE_NAME = "newsfeed"
SOURCE_TYPE = "news"

REQUEST_TIMEOUT = 20
REQUEST_DELAY_SECONDS = 1.0
#: Long summaries are mostly boilerplate and cost Gemini tokens for no gain.
MAX_SUMMARY_CHARS = 700


@dataclass(frozen=True)
class Feed:
    """One publication. `geography` is known from the masthead, so unlike the
    job boards these rows never need it inferred from the text."""

    name: str
    url: str
    geography: str


#: The free RSS sources from the data-sources guide that actually serve a feed,
#: one per market. Both were checked live before being made the default.
#:
#: Australian Mining and Infrastructure Magazine were previously written off
#: here as refusing every non-browser client "User-Agent or not". That was
#: wrong, or has stopped being true. Both sit behind a WAF that refuses an agent
#: string it does not recognise as a browser, and the conventional bot form —
#: `Mozilla/5.0 (compatible; Name/version; +url)`, which is what Googlebot and
#: Bingbot send — is accepted where the bare `MIOS/0.2` was not. Measured:
#:
#:                        MIOS/0.2   compatible-form   curl   Chrome
#:   Australian Mining       403           200          403     200
#:   Infrastructure Mag      403           200          403     200
#:   Mining.com.au           200           200          200     403
#:
#: Note the last row. Sending a Chrome string everywhere would have unblocked
#: two feeds and broken a third, so the agent below is the one that satisfies
#: all of them while still saying honestly what it is.
#:
#: robots.txt on both formerly-blocked sites is `User-agent: * / Disallow:` —
#: they permit crawling; the 403 was a crude rule about agent strings, not a
#: stated policy.
#:
#: PNG Business News serves no feed at all — /feed/, /rss and /rss.xml are all
#: 404 — so it is scraped from its category pages instead. See
#: `scraper/pngbusinessnews.py`.
#:
#: Mining Technology (https://www.mining-technology.com/feed/) does serve a
#: feed, but its coverage is global; its headlines are as often about US or
#: Canadian projects as Australian ones, which is noise for a business
#: recruiting into AU and PNG. Add it via NEWS_FEEDS if that changes.
FEEDS: tuple[Feed, ...] = (
    Feed("Mining.com.au", "https://mining.com.au/feed/", "AU"),
    Feed("Australian Mining", "https://www.australianmining.com.au/feed/", "AU"),
    Feed("Infrastructure Magazine", "https://infrastructuremagazine.com.au/feed/", "AU"),
    Feed("Business Advantage PNG", "https://www.businessadvantagepng.com/feed/", "PNG"),
)

#: Headline words that suggest an article is lifestyle or leisure coverage
#: rather than industry.
#:
#: This exists because Business Advantage PNG is a general business title. Its
#: mining, energy and company coverage is exactly what MIOS wants — "Papua LNG
#: gas agreement", "People Moves: Great Pacific Gold, Tolu Minerals" — and it
#: runs those beside Fiji airline interviews and hotel reviews, which the
#: classifier then spends a model call deciding to discard.
OFF_TOPIC_TITLE_WORDS: tuple[str, ...] = (
    "hotel", "resort", "restaurant", "cuisine", "dining", "tourism", "tourist",
    "airline", "airways", "holiday", "cruise", "art exhibition", "festival",
    "fashion", "recipe", "review",
)

#: Words that mean a headline is about building, digging or hiring something,
#: whatever else it mentions.
#:
#: A keyword list on its own is too blunt to run alone, and this is not
#: hypothetical: "$249.7M Australian Institute of Sport redevelopment enters
#: construction phase" was dropped by the word "sport" on the first run. That is
#: a quarter-billion-dollar construction project — precisely the signal this
#: pipeline exists to find — discarded by a filter meant to remove hotel
#: reviews.
#:
#: So an off-topic word only disqualifies a headline that carries no industrial
#: language at all. The asymmetry is deliberate: noise costs one classifier
#: call, which is bounded and visible in the usage counter, while a dropped
#: signal is invisible and nobody ever learns it existed. When the two are not
#: equally costly the filter should not treat them as if they were.
INDUSTRIAL_TITLE_WORDS: tuple[str, ...] = (
    "construction", "build", "building", "redevelopment",
    "development", "project", "contract", "award",
    "tender", "infrastructure", "mine", "mining", "miner", "drilling", "gas",
    "lng", "oil", "petroleum", "energy", "power", "grid", "pipeline", "port",
    "rail", "road", "highway", "bridge", "plant", "refinery", "smelter",
    "shutdown", "maintenance", "engineering", "workforce", "jobs", "hiring",
    "recruit", "appoints", "appointment", "expansion", "upgrade", "investment",
    "acquisition", "merger", "drill", "exploration", "resource", "production",
)


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    """Word-bounded match, tolerating a plural or past-tense ending.

    Word-bounded so "sport" never matches inside "transport". The optional
    ending is what makes the lists readable: without it "restaurants" and
    "reviewed" both slipped past a list containing "restaurant" and "review",
    and the alternative is spelling out every inflection of forty words.
    """
    lowered = (text or "").lower()
    return any(re.search(rf"(?<!\w){re.escape(w)}(?:s|es|ed)?(?!\w)", lowered)
               for w in words)


def is_off_topic(title: str) -> bool:
    """Whether a headline is leisure coverage and nothing more.

    Both halves matter. An off-topic word alone is not enough — a hotel being
    *built* is a construction signal, and an institute of sport being
    *redeveloped* is a contract — so a headline is only dropped when it carries
    leisure language and no industrial language whatsoever.
    """
    return _has_word(title, OFF_TOPIC_TITLE_WORDS) and not _has_word(
        title, INDUSTRIAL_TITLE_WORDS)

#: Sent on every feed request. Honest about what it is — the name and a contact
#: URL are both in there — while taking the shape publishers' filters expect.
USER_AGENT = "Mozilla/5.0 (compatible; MIOS/0.2; +https://easyskill.com.au)"

#: Atom uses a namespace, RSS does not; strip it rather than branching on it.
_NS = re.compile(r"^\{[^}]+\}")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _local(tag: str) -> str:
    return _NS.sub("", tag)


def _text(el: ElementTree.Element | None) -> str:
    if el is None:
        return ""
    # Atom summaries can be HTML fragments; RSS descriptions almost always are.
    return _WS.sub(" ", _TAG.sub(" ", el.text or "")).strip()


def _find(entry: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    """First child whose local name matches, namespace ignored."""
    wanted = {n.lower() for n in names}
    for child in entry:
        if _local(child.tag).lower() in wanted:
            return child
    return None


def _link_of(entry: ElementTree.Element) -> str:
    """RSS puts the URL in the element text; Atom puts it in an href attribute,
    often alongside `rel="replies"` and other links that are not the article."""
    for child in entry:
        if _local(child.tag).lower() != "link":
            continue
        href = child.get("href")
        if href and child.get("rel", "alternate") == "alternate":
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    guid = _find(entry, "guid", "id")
    text = (guid.text or "").strip() if guid is not None else ""
    return text if text.startswith("http") else ""


def parse_feed(xml_text: str, feed: Feed) -> list[dict[str, Any]]:
    """Feed XML -> signal records. Pure, so it is testable from a fixture.

    Malformed XML yields [] rather than raising: a publication serving a broken
    feed must not take down the sources that are fine.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        log.warning("newsfeed: %s served unparseable XML (%s)", feed.name, exc)
        return []

    out: list[dict[str, Any]] = []
    dropped: list[str] = []
    for entry in root.iter():
        if _local(entry.tag).lower() not in {"item", "entry"}:
            continue

        title = _text(_find(entry, "title"))
        if not title:
            continue
        # Dropped here rather than after collection, so an off-topic headline
        # never reaches the classifier and never spends a model call being
        # rejected. Counted in the log: a filter that silently removes most of
        # a feed is a filter somebody needs to look at.
        if is_off_topic(title):
            dropped.append(title)
            continue
        summary = _text(_find(entry, "description", "summary", "content"))
        if len(summary) > MAX_SUMMARY_CHARS:
            summary = summary[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
        link = _link_of(entry)
        posted = _text(_find(entry, "pubdate", "published", "updated")) or None

        # The publication name is part of the signal: "reported by Business
        # Advantage PNG" tells the classifier this is market intelligence
        # rather than a vacancy, which is the whole reason this source exists.
        raw_content = " | ".join(p for p in (title, feed.name, summary) if p)

        out.append({
            # Falling back to the feed URL alone would make every item in a
            # linkless feed collide on the dedupe index, leaving one survivor.
            "source_url": link or f"{feed.url}#{title[:120]}",
            "raw_content": raw_content,
            "captured_at": _now_iso(),
            "posted": posted,
            "title": title,
            "publication": feed.name,
            "source_name": SOURCE_NAME,
            "source_type": SOURCE_TYPE,
            "geography": feed.geography,
        })

    if dropped:
        # Named, not just counted. A publication that has drifted, or a keyword
        # that has started catching real stories, both show up here first.
        log.info("newsfeed.parse_feed: %s -> %d articles, %d off-topic dropped (%s)",
                 feed.name, len(out), len(dropped),
                 "; ".join(t[:48] for t in dropped[:3]))
    else:
        log.info("newsfeed.parse_feed: %s -> %d articles", feed.name, len(out))
    return out


def _configured_feeds() -> tuple[Feed, ...]:
    """`NEWS_FEEDS` overrides the defaults, as `name|url|geography` entries."""
    raw = settings.news_feeds
    if not raw:
        return FEEDS
    feeds: list[Feed] = []
    for spec in raw:
        parts = [p.strip() for p in spec.split("|")]
        if len(parts) != 3 or not all(parts):
            log.warning(
                "newsfeed: ignoring malformed NEWS_FEEDS entry %r "
                "(expected Name|https://url/feed|AU)",
                spec,
            )
            continue
        feeds.append(Feed(parts[0], parts[1], parts[2].upper()))
    return tuple(feeds) or FEEDS


def _scrape_sync(limit: int, feeds: tuple[Feed, ...]) -> list[dict[str, Any]]:
    per_feed: list[list[dict[str, Any]]] = []

    for i, feed in enumerate(feeds):
        if i:
            time.sleep(REQUEST_DELAY_SECONDS)
        try:
            res = requests.get(
                feed.url,
                timeout=REQUEST_TIMEOUT,
                # The conventional bot form. Still says plainly what this is
                # and where to complain; the `Mozilla/5.0 (compatible; ...)`
                # wrapper is what the publishers' WAFs actually check for. See
                # the table above FEEDS for what each one accepts.
                headers={"User-Agent": USER_AGENT},
            )
            res.raise_for_status()
            per_feed.append(parse_feed(res.text, feed))
        except Exception as exc:  # noqa: BLE001 - one dead feed, not a dead run
            log.warning("newsfeed: %s unreachable (%s) — skipping", feed.name, exc)
            per_feed.append([])

    # Round-robin rather than draining feed one before touching feed two, so a
    # prolific publication cannot consume the whole limit — the same reason the
    # digest interleaves its sources.
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = 0
    while len(collected) < limit and any(cursor < len(f) for f in per_feed):
        for items in per_feed:
            if len(collected) >= limit:
                break
            if cursor >= len(items):
                continue
            rec = items[cursor]
            if rec["source_url"] in seen:
                continue
            seen.add(rec["source_url"])
            collected.append(rec)
        cursor += 1

    log.info("newsfeed: %d articles from %d feed(s)", len(collected), len(feeds))
    return collected


async def scrape_async(limit: int = 50, base_url: str | None = None) -> list[dict[str, Any]]:
    """Never raises; returns [] on any failure, like the other sources.

    `base_url` overrides the feed list with a single feed, which is what
    `--source newsfeed --base-url ...` means for this source.
    """
    feeds = (Feed("Override", base_url, "AU"),) if base_url else _configured_feeds()
    if not feeds:
        log.warning("newsfeed: no feeds configured — skipping")
        return []
    try:
        return await asyncio.to_thread(_scrape_sync, limit, feeds)
    except Exception as exc:  # noqa: BLE001
        log.warning("newsfeed: scrape failed (%s) — returning []", exc)
        return []
