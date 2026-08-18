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
#: Australian Mining, Energy Magazine, Infrastructure Magazine and Roads &
#: Infrastructure are all on the same publisher's network and every one of them
#: answers 403 to a non-browser client, User-Agent or not. Defeating that is the
#: browser-automation work this source exists to avoid, so they are left out
#: rather than shipped as a default that fails on every run. Defence Connect and
#: PNG Business News answer 404 — the URLs in the guide are stale.
#:
#: Mining Technology (https://www.mining-technology.com/feed/) does serve a
#: feed, but its coverage is global; its headlines are as often about US or
#: Canadian projects as Australian ones, which is noise for a business
#: recruiting into AU and PNG. Add it via NEWS_FEEDS if that changes.
FEEDS: tuple[Feed, ...] = (
    Feed("Mining.com.au", "https://mining.com.au/feed/", "AU"),
    Feed("Business Advantage PNG", "https://www.businessadvantagepng.com/feed/", "PNG"),
)

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
    for entry in root.iter():
        if _local(entry.tag).lower() not in {"item", "entry"}:
            continue

        title = _text(_find(entry, "title"))
        if not title:
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
                # Some publishers return 403 to an unidentified client.
                headers={"User-Agent": "MIOS/0.2 (+market intelligence; Easy Skill Australia)"},
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
