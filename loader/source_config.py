"""What the collectors need to be told, set from the Admin panel.

Five things: the Apify token, which actor reads which job board, the most one
run of an actor may cost, the ASX companies to follow, and any extra news
feeds. All five are set from the panel and nowhere else.

Same rules as every other setting in the panel:

* **The token is a secret.** It is stored encrypted with MIOS_CREDENTIAL_KEY
  (see `loader.credentials`) and never returned — only its last four characters.
* **There is no environment fallback.** Nothing here is read from a server
  variable. Unset means unset: an Apify board with no token or actor is "not
  configured" and never runs, ASX follows its built-in list of companies, and
  there are no custom feeds. The app works the same either way.
* **Everything is validated on the way in.** A setting that is wrong fails when
  it is saved, with a message, rather than at five on a Monday morning as a
  source that quietly collected nothing.

This lives in `loader` because the pipeline's collectors read it and the API
writes it, like `loader.source_settings`.

**Read through a short cache.** A collector asks for its settings once a run,
but the signal feed asks "are there custom feeds?" for every row it names, and
the Data sources page asks whether each of eight boards is configured. One read
serves all of that for a few seconds; a write clears it.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import loader.db as _db
from loader.db import connect

log = logging.getLogger(__name__)

KEY_NAME = "apify"
KV_KEY = "sources:config"

CACHE_SECONDS = 15.0
#: The most one run of one actor may be charged, in US dollars. Applied to every
#: run whether or not anybody has set it: an actor is somebody else's program on
#: a metered account, and "no limit" is not a safe thing to default to. A board's
#: results at a couple of dollars per thousand cost cents, so a dollar is room
#: to spare and still a ceiling.
DEFAULT_RUN_CHARGE_USD = 1.00
MIN_RUN_CHARGE_USD = 0.05
MAX_RUN_CHARGE_USD = 50.00
MAX_TICKERS = 60
MAX_FEEDS = 20
MARKETS = ("AU", "PNG")

#: user/actor, or Apify's own user~actor form.
_ACTOR = re.compile(r"^[A-Za-z0-9][\w.-]*[/~][A-Za-z0-9][\w.-]*$")
_TICKER = re.compile(r"^[A-Z0-9]{2,6}$")


class SourceConfigError(ValueError):
    """Bad input from the panel. The message is shown to the administrator."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def forget() -> None:
    """Drop the cache. Called after every write; tests call it between cases."""
    _cache.clear()


def _read(target) -> dict[str, Any]:
    """The stored settings and the token in play, for a few seconds at most."""
    # Looked up on the module each time, not imported by name: the tests swap
    # `loader.db.resolve_target` to keep every test on its own scratch database,
    # and a reference held here would miss that and share one cache between them.
    key = str(_db.resolve_target(target))
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]

    blob: dict[str, Any] = {}
    try:
        with connect(target, readonly=True) as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (KV_KEY,)).fetchone()
        if row and row["value"]:
            parsed = json.loads(row["value"])
            if isinstance(parsed, dict):
                blob = parsed
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.debug("source_config: could not read (%s)", exc)

    from loader.credentials import key_for

    try:
        token = key_for(KEY_NAME, target)
    except Exception as exc:  # noqa: BLE001 - an unreadable key is "no key"
        log.debug("source_config: could not read the Apify token (%s)", exc)
        token = ""

    state = {"blob": blob, "token": token}
    _cache[key] = (time.monotonic() + CACHE_SECONDS, state)
    return state


def _write(mutate, target) -> None:
    blob = dict(_read(target)["blob"])
    mutate(blob)
    body = json.dumps(blob)
    with connect(target) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO kv_store (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = ?",
            (KV_KEY, body, body),
        )
    forget()


# --------------------------------------------------------------------------
# Apify
# --------------------------------------------------------------------------


def apify_token(target=None) -> str:
    """The token entered in the panel, or "" when there is none."""
    return _read(target)["token"]


def _boards() -> dict[str, Any]:
    from scraper import catalog

    return {s.id: s for s in catalog.APIFY_BOARDS}


def _board(source_id: str, target) -> dict[str, Any]:
    return (_read(target)["blob"].get("apifyBoards") or {}).get(source_id) or {}


def apify_actor(source_id: str, target=None) -> str:
    """The actor named for a board, or "" when there is none."""
    return str(_board(source_id, target).get("actor") or "")


def apify_input(source_id: str, target=None) -> dict[str, Any]:
    """The actor's own input for a board, as entered beside the actor."""
    stored = _board(source_id, target).get("input")
    return dict(stored) if isinstance(stored, dict) else {}


def apify_max_charge(target=None) -> float:
    """The most one run of an actor may be charged, in US dollars."""
    stored = (_read(target)["blob"].get("apifyRun") or {}).get("maxChargeUsd")
    try:
        value = float(stored)
    except (TypeError, ValueError):
        return DEFAULT_RUN_CHARGE_USD
    # A stored value outside the range can only be an old or hand-edited one.
    return value if MIN_RUN_CHARGE_USD <= value <= MAX_RUN_CHARGE_USD else DEFAULT_RUN_CHARGE_USD


def set_apify_max_charge(raw: Any, *, changed_by: str, target=None) -> float:
    """Set the per-run ceiling. Empty returns to the default."""
    text = "" if raw is None else str(raw).strip().lstrip("$").strip()

    if not text:
        def drop(blob: dict[str, Any]) -> None:
            blob.pop("apifyRun", None)
        _write(drop, target)
        log.info("source_config: %s reset the Apify run ceiling", changed_by)
        return DEFAULT_RUN_CHARGE_USD

    try:
        value = round(float(text), 2)
    except ValueError as exc:
        raise SourceConfigError(
            f"'{raw}' is not an amount. Enter dollars, like 1.50.") from exc
    if not MIN_RUN_CHARGE_USD <= value <= MAX_RUN_CHARGE_USD:
        raise SourceConfigError(
            f"The limit must be between ${MIN_RUN_CHARGE_USD:.2f} and ${MAX_RUN_CHARGE_USD:.2f} a run.")

    def put(blob: dict[str, Any]) -> None:
        blob["apifyRun"] = {"maxChargeUsd": value, "changedBy": changed_by, "changedAt": _now()}
    _write(put, target)
    log.info("source_config: %s set the Apify run ceiling to $%.2f", changed_by, value)
    return value


def set_apify_token(token: str, *, changed_by: str, target=None) -> None:
    from loader.credentials import set_key

    token = (token or "").strip()
    if len(token) < 20 or " " in token:
        raise SourceConfigError(
            "That does not look like an Apify API token. Copy it from Apify Console → "
            "Settings → API & Integrations.")
    set_key(KEY_NAME, token, changed_by=changed_by, target=target)
    forget()


def clear_apify_token(target=None) -> bool:
    from loader.credentials import clear_key

    removed = clear_key(KEY_NAME, target)
    forget()
    return removed


def set_board(source_id: str, actor: str, input_text: str | None, *,
              changed_by: str, target=None) -> None:
    """Name the actor for one board, with the actor's own input. An empty actor
    forgets the board's setting, leaving it not configured."""
    if source_id not in _boards():
        raise SourceConfigError(f"'{source_id}' is not a board that is read through Apify.")
    actor = (actor or "").strip()

    if not actor:
        def drop(blob: dict[str, Any]) -> None:
            boards = dict(blob.get("apifyBoards") or {})
            boards.pop(source_id, None)
            blob["apifyBoards"] = boards
        _write(drop, target)
        log.info("source_config: %s cleared the actor for %s", changed_by, source_id)
        return

    if not _ACTOR.match(actor):
        raise SourceConfigError(
            "An actor is written as username/actor-name, exactly as it appears in the "
            "Apify Store address — for example misceres/indeed-scraper.")

    actor_input: dict[str, Any] = {}
    text = (input_text or "").strip()
    if text:
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise SourceConfigError(f"The search settings are not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise SourceConfigError(
                'The search settings must be a JSON object, like {"position": "mining"}.')
        actor_input = parsed

    def put(blob: dict[str, Any]) -> None:
        boards = dict(blob.get("apifyBoards") or {})
        boards[source_id] = {"actor": actor, "input": actor_input,
                             "changedBy": changed_by, "changedAt": _now()}
        blob["apifyBoards"] = boards
    _write(put, target)
    log.info("source_config: %s set the actor for %s to %s", changed_by, source_id, actor)


def test_apify_token(target=None) -> dict[str, Any]:
    """Ask Apify who the token belongs to. Costs nothing and runs no actor."""
    import requests

    token = apify_token(target)
    if not token:
        raise SourceConfigError("There is no token to test. Add one first.")
    try:
        res = requests.get("https://api.apify.com/v2/users/me", timeout=20,
                           headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"Apify could not be reached ({type(exc).__name__})."}
    if res.status_code == 401:
        return {"ok": False, "detail": "Apify rejected the token (401). Check it was copied in full."}
    if res.status_code != 200:
        return {"ok": False, "detail": f"Apify answered {res.status_code}."}
    try:
        data = (res.json() or {}).get("data") or {}
    except ValueError:
        data = {}
    who = data.get("username") or data.get("email") or "an Apify account"
    return {"ok": True, "detail": f"The token works. It belongs to {who}."}


# --------------------------------------------------------------------------
# ASX companies
# --------------------------------------------------------------------------


def asx_tickers(target=None) -> tuple[str, ...]:
    """The ASX codes to follow, as set in the panel. Empty means the
    collector's own built-in list."""
    return tuple((_read(target)["blob"].get("asxTickers") or {}).get("tickers") or [])


def set_asx_tickers(raw: Any, *, changed_by: str, target=None) -> tuple[str, ...]:
    """Replace the list. An empty list returns to the built-in one."""
    parts = raw if isinstance(raw, (list, tuple)) else re.split(r"[\s,;]+", str(raw or ""))
    tickers: list[str] = []
    for part in parts:
        code = str(part).strip().upper()
        if not code:
            continue
        # People paste "ASX:BHP" and "BHP.AX"; both mean BHP.
        code = code.removeprefix("ASX:").removesuffix(".AX")
        if not _TICKER.match(code):
            raise SourceConfigError(
                f"'{part}' is not an ASX code. Codes are two to six letters or digits, like BHP.")
        if code not in tickers:
            tickers.append(code)
    if len(tickers) > MAX_TICKERS:
        raise SourceConfigError(f"That is {len(tickers)} companies; the most is {MAX_TICKERS}. "
                                "Each one is a request every run.")

    def put(blob: dict[str, Any]) -> None:
        blob["asxTickers"] = {"tickers": tickers, "changedBy": changed_by, "changedAt": _now()}
    _write(put, target)
    log.info("source_config: %s set %d ASX companies", changed_by, len(tickers))
    return tuple(tickers)


# --------------------------------------------------------------------------
# Custom news feeds
# --------------------------------------------------------------------------


def custom_feeds(target=None) -> list[dict[str, str]]:
    """The extra feeds to read, as added in the panel."""
    stored = (_read(target)["blob"].get("newsFeeds") or {}).get("feeds") or []
    return [dict(f) for f in stored]


def set_custom_feeds(feeds: Any, *, changed_by: str, target=None) -> list[dict[str, str]]:
    """Replace the list. An empty list means no custom feeds."""
    if not isinstance(feeds, list):
        raise SourceConfigError("Send the feeds as a list.")
    if len(feeds) > MAX_FEEDS:
        raise SourceConfigError(f"That is {len(feeds)} feeds; the most is {MAX_FEEDS}.")

    from scraper import catalog
    from scraper.publications import _host

    catalogued = {_host(s.feed_url): s.label for s in catalog.FEEDS}
    clean: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, f in enumerate(feeds, 1):
        if not isinstance(f, dict):
            raise SourceConfigError(f"Feed {i} is not filled in.")
        name = str(f.get("name") or "").strip()
        url = str(f.get("url") or "").strip()
        market = str(f.get("market") or "").strip().upper()
        if not name:
            raise SourceConfigError(f"Feed {i} needs a name — the publication's title.")
        if len(name) > 60:
            raise SourceConfigError(f"'{name[:30]}…' is too long for a name; keep it under 60 characters.")
        if not re.match(r"^https?://[^\s/]+\.[^\s/]+", url):
            raise SourceConfigError(f"'{name}' needs a full feed address starting with https://.")
        if market not in MARKETS:
            raise SourceConfigError(f"'{name}' needs a market: AU or PNG.")
        if url in seen:
            raise SourceConfigError(f"'{name}' repeats an address already in the list.")
        if _host(url) in catalogued:
            raise SourceConfigError(
                f"'{name}' is already a source of its own ({catalogued[_host(url)]}). "
                "Switch that one on instead of adding it here.")
        seen.add(url)
        clean.append({"name": name, "url": url, "market": market})

    def put(blob: dict[str, Any]) -> None:
        blob["newsFeeds"] = {"feeds": clean, "changedBy": changed_by, "changedAt": _now()}
    _write(put, target)
    log.info("source_config: %s set %d custom feeds", changed_by, len(clean))
    return clean


def check_feed(url: str) -> dict[str, Any]:
    """Fetch a feed once and say what came back, before it is saved."""
    import requests

    from scraper import newsfeed

    url = (url or "").strip()
    if not re.match(r"^https?://[^\s/]+\.[^\s/]+", url):
        raise SourceConfigError("Enter the full feed address, starting with https://.")
    try:
        res = requests.get(url, timeout=newsfeed.REQUEST_TIMEOUT,
                           headers={"User-Agent": newsfeed.USER_AGENT})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"The address could not be reached ({type(exc).__name__})."}
    if res.status_code != 200:
        hint = " The site refuses automated readers." if res.status_code == 403 else ""
        return {"ok": False, "detail": f"The site answered {res.status_code}.{hint}"}
    articles = newsfeed.parse_feed(res.text, newsfeed.Feed("check", url, "AU"))
    if not articles:
        return {"ok": False, "detail": "The address answered, but it is not a feed MIOS can read "
                                       "(no articles were found in it)."}
    return {"ok": True,
            "detail": f"Readable: {len(articles)} article{'' if len(articles) == 1 else 's'}, "
                      f"the newest “{articles[0]['title'][:70]}”."}


# --------------------------------------------------------------------------
# For the panel
# --------------------------------------------------------------------------


def status(target=None) -> dict[str, Any]:
    """What the panel shows. Never the token itself."""
    from loader.credentials import available, describe
    from scraper import asx

    state = _read(target)
    blob = state["blob"]
    key = describe(KEY_NAME, target=target)
    has_token = bool(state["token"])

    # Each board's results per run, which is set with the other limits. Shown
    # here because it is the other half of what a run can cost.
    try:
        from loader import pipeline_settings

        limits = pipeline_settings.scrape_limits(target)
    except Exception as exc:  # noqa: BLE001 - the panel still renders without it
        log.debug("source_config: could not read the run limits (%s)", exc)
        limits = {}

    run_stored = blob.get("apifyRun") or {}
    custom_charge = "maxChargeUsd" in run_stored

    stored_boards = blob.get("apifyBoards") or {}
    boards = []
    for source_id, src in _boards().items():
        stored = stored_boards.get(source_id) or {}
        actor = str(stored.get("actor") or "")
        actor_input = stored.get("input")
        boards.append({
            "id": source_id, "label": src.label, "market": src.market, "url": src.url,
            "actor": actor,
            "input": json.dumps(actor_input, indent=2) if isinstance(actor_input, dict) and actor_input else "",
            "ready": bool(has_token and actor),
            "limit": limits.get(source_id, src.limit),
            "changedBy": stored.get("changedBy"), "changedAt": stored.get("changedAt"),
        })

    asx_stored = blob.get("asxTickers") or {}
    tickers = list(asx_stored.get("tickers") or [])
    feeds_stored = blob.get("newsFeeds") or {}
    feeds = list(feeds_stored.get("feeds") or [])

    return {
        "apify": {
            "token": {k: key.get(k) for k in ("source", "hint", "unreadable")},
            "canStoreKey": available(),
            "boards": boards,
            "readyCount": sum(1 for b in boards if b["ready"]),
            "run": {
                "maxChargeUsd": apify_max_charge(target),
                "custom": custom_charge,
                "default": DEFAULT_RUN_CHARGE_USD,
                "min": MIN_RUN_CHARGE_USD,
                "max": MAX_RUN_CHARGE_USD,
                "changedBy": run_stored.get("changedBy") if custom_charge else None,
                "changedAt": run_stored.get("changedAt") if custom_charge else None,
            },
        },
        "asx": {
            #: The list in play, and whether it is the administrator's or the
            #: built-in one.
            "tickers": tickers or list(asx.DEFAULT_TICKERS),
            "custom": bool(tickers),
            "defaults": list(asx.DEFAULT_TICKERS),
            "max": MAX_TICKERS,
            "changedBy": asx_stored.get("changedBy") if tickers else None,
            "changedAt": asx_stored.get("changedAt") if tickers else None,
        },
        "feeds": {
            "feeds": feeds,
            "max": MAX_FEEDS,
            "markets": list(MARKETS),
            "changedBy": feeds_stored.get("changedBy") if feeds else None,
            "changedAt": feeds_stored.get("changedAt") if feeds else None,
        },
    }


__all__ = [
    "SourceConfigError", "apify_actor", "apify_input", "apify_max_charge", "apify_token",
    "asx_tickers", "check_feed", "clear_apify_token", "custom_feeds", "forget",
    "set_apify_max_charge", "set_apify_token", "set_asx_tickers", "set_board",
    "set_custom_feeds", "status", "test_apify_token",
]
