"""One default search, put into whatever actor a board uses.

An actor run with no input uses its author's defaults, which for a job board
means every kind of job: the first production run of SEEK and Jora (5 Oct 2026)
brought back dental assistants, pick packers and dog walkers, and the
classifier filed 66 of 70 under "other".

So there is one default search for every board: a line of keywords, set by an
administrator under Admin › Integrations (see `loader.source_config`), which
starts as the sectors Easy Skill recruits into. A board uses it whenever it has
no search settings of its own.

**The same search, in each actor's own words.** Every actor names its input
differently — `searchTerm`, `keyword`, `position` — and the same goes for "how
many results". Rather than a table of actors, the names are read from the
actor's own input schema, which Apify publishes for every actor. That works
for an actor nobody here has seen, and keeps working when an author renames a
field.

When the schema cannot be read (Apify is unreachable, the actor is private and
the token cannot see it), the keywords are sent under the handful of names
actors most often use. An actor ignores input it does not know, so the wrong
guesses cost nothing; it is the fallback, not the method.

Keywords are joined with OR, which SEEK, Indeed and LinkedIn read as "any of
these". A board that does not will match less, and an administrator can then
give that board its own search settings or shorten the default.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)

SCHEMA_URL = "https://api.apify.com/v2/acts/{actor}/builds/default"
SCHEMA_TIMEOUT = 15

#: What Easy Skill recruits into, as one search. The starting value of the
#: setting, not a constant the collectors read.
DEFAULT_KEYWORDS = "mining OR oil and gas OR energy OR construction OR defence"

#: Names actors give the keyword field, most specific first. Compared without
#: case, spaces, hyphens or underscores.
KEYWORD_NAMES = ("searchterm", "searchterms", "keyword", "keywords", "searchquery",
                 "searchqueries", "search", "query", "queries", "position", "positions",
                 "jobtitle", "jobtitles", "title", "titles", "q", "what")
#: And the field for "stop after this many".
COUNT_NAMES = ("maxresults", "resultswanted", "maxitems", "maxjobs", "maxrows", "rows",
               "numberofresults", "resultslimit", "maxposts", "limit", "count")

#: Sent when the schema is unavailable. Strings only: these are the names that
#: are a plain string in every actor seen, so a wrong guess is ignored rather
#: than rejected for its type.
FALLBACK_KEYWORD_FIELDS = ("searchTerm", "keyword", "search", "query", "position")


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-]", "", name or "").lower()


@dataclass(frozen=True)
class Fields:
    """Where an actor wants the search and the limit put."""

    #: The keyword field, or None when the actor has none (it takes addresses).
    keyword: str | None = None
    #: True when that field is a list of terms rather than one string.
    keyword_is_list: bool = False
    #: The actor's own "how many results" field, or None.
    count: str | None = None
    #: The most the actor accepts there, where it says.
    count_max: int | None = None
    #: False when the schema could not be read and the names are guesses.
    known: bool = True


UNKNOWN = Fields(known=False)


def fields_from_schema(schema: Any) -> Fields:
    """Pick the keyword and count fields out of an actor's input schema. Pure."""
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict) or not props:
        return UNKNOWN
    by_norm = {_norm(k): k for k in props}

    keyword, is_list = None, False
    for name in KEYWORD_NAMES:
        key = by_norm.get(name)
        kind = (props.get(key) or {}).get("type") if key else None
        if kind in ("string", "array"):
            keyword, is_list = key, kind == "array"
            break

    count, count_max = None, None
    for name in COUNT_NAMES:
        key = by_norm.get(name)
        spec = props.get(key) or {} if key else {}
        if spec.get("type") == "integer":
            count = key
            top = spec.get("maximum")
            count_max = int(top) if isinstance(top, (int, float)) and top > 0 else None
            break

    return Fields(keyword=keyword, keyword_is_list=is_list, count=count, count_max=count_max)


def _http_get(url: str, **kwargs: Any) -> Any:
    return requests.get(url, **kwargs)


def fetch_fields(actor: str, token: str = "") -> Fields:
    """Read an actor's input schema from Apify. Never raises."""
    try:
        res = _http_get(
            SCHEMA_URL.format(actor=str(actor).replace("/", "~")), timeout=SCHEMA_TIMEOUT,
            headers={"Authorization": f"Bearer {token}"} if token else {})
        if res.status_code != 200:
            log.info("apify: no input schema for %s (HTTP %s)", actor, res.status_code)
            return UNKNOWN
        data = (res.json() or {}).get("data") or {}
        raw = data.get("inputSchema") or (data.get("actorDefinition") or {}).get("input")
        schema = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:  # noqa: BLE001 - the fallback covers it
        log.info("apify: could not read the input schema for %s (%s)", actor, type(exc).__name__)
        return UNKNOWN
    return fields_from_schema(schema)


def terms(keywords: str) -> list[str]:
    """The separate terms in a line of keywords joined with OR."""
    parts = (p.strip().strip('"') for p in re.split(r"\s+OR\s+|\s*[,;|]\s*", keywords or ""))
    # An OR with nothing either side of it is not a keyword.
    return [p for p in parts if p and p != "OR"]


def search_input(fields: Fields, keywords: str) -> dict[str, Any]:
    """The default search as an actor's input."""
    keywords = (keywords or "").strip()
    if not keywords:
        return {}
    if not fields.known:
        return {name: keywords for name in FALLBACK_KEYWORD_FIELDS}
    if not fields.keyword:
        return {}
    return {fields.keyword: terms(keywords) if fields.keyword_is_list else keywords}


def with_limit(body: dict[str, Any], fields: Fields, limit: int) -> dict[str, Any]:
    """Put the board's limit where the actor will read it.

    `maxItems` always, which many actors read. And the actor's own field when
    it has a different one, so it stops there: one SEEK actor fetches 300
    results unless its `maxResults` says otherwise.
    """
    body["maxItems"] = limit
    if fields.count and fields.count != "maxItems":
        body[fields.count] = min(limit, fields.count_max) if fields.count_max else limit
    return body
