"""What the collectors are told, set from the Admin panel.

Pinned: nothing is read from the environment, an empty panel leaves the app
working (boards not configured, ASX on its built-in list, no custom feeds), the
token is stored encrypted and never returned, bad input fails when it is saved
with a message an administrator can act on, and only administrators can change
any of it. Apify and the feed's site are replaced by fakes.
"""
from __future__ import annotations

import dataclasses
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from config.settings import settings as real_settings
from loader import source_settings
from loader.db import connect, resolve_target
from scraper import apify, asx, catalog

TOKEN = "apify_api_test-only-secret-token-wxyz"
BOARD = catalog.APIFY_BOARDS[0].id
ADMIN = "admin@easyskill.com"

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Mine expansion approved in the Pilbara</title>
<link>https://example.com/a</link><description>Body</description>
<pubDate>Mon, 05 Oct 2026 01:00:00 GMT</pubDate></item></channel></rss>"""


# ---------- nothing entered ----------


def test_an_empty_panel_leaves_everything_working(panel):
    """The whole point of "no fallback, but the app still works"."""
    assert panel.apify_token() == ""
    assert panel.apify_actor(BOARD) == "" and panel.apify_input(BOARD) == {}
    assert panel.asx_tickers() == () and panel.custom_feeds() == []

    assert asx.tickers() == asx.DEFAULT_TICKERS, "ASX follows its built-in list"
    ok, why = apify.configured(BOARD)
    assert not ok and "Integrations" in why
    assert source_settings.default_enabled(BOARD) is False
    assert source_settings.configured("newsfeed")[0] is False

    st = panel.status()
    assert st["apify"]["token"] == {"source": "none", "hint": None, "unreadable": False}
    assert st["apify"]["readyCount"] == 0
    assert {b["id"] for b in st["apify"]["boards"]} == {s.id for s in catalog.APIFY_BOARDS}
    assert st["asx"]["custom"] is False and st["asx"]["tickers"] == list(asx.DEFAULT_TICKERS)
    assert st["feeds"]["feeds"] == []


def test_the_old_environment_variables_do_nothing(panel, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", TOKEN)
    monkeypatch.setenv("APIFY_ACTORS", f"{BOARD}=someone/some-actor")
    monkeypatch.setenv("APIFY_INPUTS", json.dumps({BOARD: {"q": "mining"}}))
    monkeypatch.setenv("ASX_TICKERS", "ZZZ")
    monkeypatch.setenv("NEWS_FEEDS", "Paper|https://example.com/feed|AU")
    panel.forget()

    assert panel.apify_token() == "" and panel.apify_actor(BOARD) == ""
    assert panel.apify_input(BOARD) == {}
    assert asx.tickers() == asx.DEFAULT_TICKERS
    assert panel.custom_feeds() == []


# ---------- Apify ----------


def test_the_token_is_encrypted_and_never_returned(panel):
    panel.set_apify_token(f"  {TOKEN}  ", changed_by=ADMIN)
    assert panel.apify_token() == TOKEN, "pasted whitespace is dropped"

    st = panel.status()
    assert "test-only-secret" not in json.dumps(st)
    assert st["apify"]["token"] == {"source": "panel", "hint": "wxyz", "unreadable": False}
    with connect(resolve_target(None), readonly=True) as conn:
        rows = json.dumps([dict(r) for r in conn.execute("SELECT * FROM llm_credentials")])
    assert "test-only-secret" not in rows

    assert panel.clear_apify_token() is True
    assert panel.apify_token() == ""
    assert panel.clear_apify_token() is False


@pytest.mark.parametrize("bad", ["", "short", "has a space in the middle of it ok"])
def test_something_that_is_not_a_token_is_refused(panel, bad):
    with pytest.raises(panel.SourceConfigError, match="Apify API token"):
        panel.set_apify_token(bad, changed_by=ADMIN)


def test_a_board_needs_both_a_token_and_an_actor(panel):
    panel.set_board(BOARD, "someone/board-reader", '{"position": "mining"}', changed_by=ADMIN)
    assert panel.apify_actor(BOARD) == "someone/board-reader"
    assert panel.apify_input(BOARD) == {"position": "mining"}
    ok, why = apify.configured(BOARD)
    assert not ok and "token" in why, "an actor alone is not enough"

    panel.set_apify_token(TOKEN, changed_by=ADMIN)
    assert apify.configured(BOARD) == (True, None)
    assert source_settings.default_enabled(BOARD) is True, "naming the actor switches it on"

    board = next(b for b in panel.status()["apify"]["boards"] if b["id"] == BOARD)
    assert board["ready"] is True and board["changedBy"] == ADMIN
    assert json.loads(board["input"]) == {"position": "mining"}
    assert panel.status()["apify"]["readyCount"] == 1

    # An empty actor forgets the board, input and all.
    panel.set_board(BOARD, "", None, changed_by=ADMIN)
    assert panel.apify_actor(BOARD) == "" and panel.apify_input(BOARD) == {}
    assert apify.configured(BOARD)[0] is False


def test_the_tilde_form_of_an_actor_name_is_accepted(panel):
    panel.set_board(BOARD, "someone~board-reader", "", changed_by=ADMIN)
    assert panel.apify_actor(BOARD) == "someone~board-reader"


@pytest.mark.parametrize("actor,text,message", [
    ("just-a-name", "", "username/actor-name"),
    ("https://apify.com/someone/reader", "", "username/actor-name"),
    ("someone/reader", "{not json", "not valid JSON"),
    ("someone/reader", '["a", "b"]', "JSON object"),
])
def test_a_board_setting_that_is_wrong_fails_when_saved(panel, actor, text, message):
    with pytest.raises(panel.SourceConfigError, match=message):
        panel.set_board(BOARD, actor, text, changed_by=ADMIN)
    assert panel.apify_actor(BOARD) == "", "nothing half-saved"


def test_only_boards_read_through_apify_can_be_given_an_actor(panel):
    with pytest.raises(panel.SourceConfigError, match="not a board"):
        panel.set_board("pngworkforce", "someone/reader", "", changed_by=ADMIN)


def test_testing_the_token_reports_apifys_own_answer(panel):
    with pytest.raises(panel.SourceConfigError, match="no token"):
        panel.test_apify_token()

    panel.set_apify_token(TOKEN, changed_by=ADMIN)
    fine = MagicMock(status_code=200, json=lambda: {"data": {"username": "easyskill"}})
    with patch("requests.get", return_value=fine) as get:
        assert panel.test_apify_token() == {
            "ok": True, "detail": "The token works. It belongs to easyskill."}
    # In a header, never the address, so it cannot end up in a log.
    assert TOKEN not in get.call_args.args[0]
    assert get.call_args.kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"

    with patch("requests.get", return_value=MagicMock(status_code=401)):
        result = panel.test_apify_token()
    assert result["ok"] is False and "rejected" in result["detail"]

    with patch("requests.get", side_effect=ConnectionError("down")):
        result = panel.test_apify_token()
    assert result["ok"] is False and "could not be reached" in result["detail"]


def test_a_run_is_capped_by_cost_whether_or_not_anyone_set_it(panel):
    """No limit is not a safe default for somebody else's program on a metered
    account, so there is always one."""
    assert panel.apify_max_charge() == panel.DEFAULT_RUN_CHARGE_USD
    run = panel.status()["apify"]["run"]
    assert run["custom"] is False and run["changedBy"] is None
    assert run["maxChargeUsd"] == run["default"] == panel.DEFAULT_RUN_CHARGE_USD

    assert panel.set_apify_max_charge(" $2.5 ", changed_by=ADMIN) == 2.5
    assert panel.apify_max_charge() == 2.5
    run = panel.status()["apify"]["run"]
    assert run["custom"] is True and run["changedBy"] == ADMIN and run["maxChargeUsd"] == 2.5

    assert panel.set_apify_max_charge("", changed_by=ADMIN) == panel.DEFAULT_RUN_CHARGE_USD
    assert panel.status()["apify"]["run"]["custom"] is False


@pytest.mark.parametrize("bad,message", [
    ("a lot", "not an amount"),
    ("0", "must be between"),
    ("0.01", "must be between"),
    ("500", "must be between"),
    ("-1", "must be between"),
])
def test_a_spending_limit_that_is_wrong_is_refused(panel, bad, message):
    with pytest.raises(panel.SourceConfigError, match=message):
        panel.set_apify_max_charge(bad, changed_by=ADMIN)
    assert panel.apify_max_charge() == panel.DEFAULT_RUN_CHARGE_USD


def test_each_board_shows_the_results_it_takes_per_run(panel):
    boards = {b["id"]: b for b in panel.status()["apify"]["boards"]}
    assert boards["seek"]["limit"] == 50 and boards["indeed"]["limit"] == 30


# ---------- ASX ----------


def test_asx_codes_are_tidied_and_can_be_reset(panel):
    saved = panel.set_asx_tickers("bhp, ASX:RIO  fmg.ax\nBHP", changed_by=ADMIN)
    assert saved == ("BHP", "RIO", "FMG"), "upper-cased, prefixes dropped, repeats removed"
    assert asx.tickers() == ("BHP", "RIO", "FMG")

    st = panel.status()["asx"]
    assert st["custom"] is True and st["tickers"] == ["BHP", "RIO", "FMG"]
    assert st["changedBy"] == ADMIN

    assert panel.set_asx_tickers([], changed_by=ADMIN) == ()
    assert asx.tickers() == asx.DEFAULT_TICKERS
    assert panel.status()["asx"]["custom"] is False


def test_asx_codes_that_are_wrong_are_refused(panel):
    with pytest.raises(panel.SourceConfigError, match="not an ASX code"):
        panel.set_asx_tickers("BHP, Rio Tinto Limited!", changed_by=ADMIN)
    too_many = " ".join(f"A{i:02d}" for i in range(panel.MAX_TICKERS + 1))
    with pytest.raises(panel.SourceConfigError, match="the most is"):
        panel.set_asx_tickers(too_many, changed_by=ADMIN)
    assert panel.asx_tickers() == ()


# ---------- custom feeds ----------


def test_adding_a_feed_switches_the_custom_source_on(panel):
    saved = panel.set_custom_feeds(
        [{"name": " The National ", "url": "https://thenational.example/feed", "market": "png"}],
        changed_by=ADMIN)
    assert saved == [{"name": "The National", "url": "https://thenational.example/feed",
                      "market": "PNG"}]
    assert source_settings.configured("newsfeed") == (True, None)
    assert source_settings.default_enabled("newsfeed") is True

    panel.set_custom_feeds([], changed_by=ADMIN)
    assert source_settings.configured("newsfeed")[0] is False


@pytest.mark.parametrize("feeds,message", [
    ("not a list", "as a list"),
    ([{"name": "", "url": "https://a.example/feed", "market": "AU"}], "needs a name"),
    ([{"name": "A", "url": "a.example/feed", "market": "AU"}], "full feed address"),
    ([{"name": "A", "url": "https://a.example/feed", "market": "NZ"}], "AU or PNG"),
    ([{"name": "A", "url": "https://a.example/feed", "market": "AU"},
      {"name": "B", "url": "https://a.example/feed", "market": "AU"}], "repeats"),
])
def test_a_feed_that_is_wrong_fails_when_saved(panel, feeds, message):
    with pytest.raises(panel.SourceConfigError, match=message):
        panel.set_custom_feeds(feeds, changed_by=ADMIN)
    assert panel.custom_feeds() == []


def test_a_catalogued_publication_cannot_be_added_again(panel):
    """It already has a row and a switch of its own; a second copy would
    collect every article twice."""
    known = catalog.FEEDS[0]
    with pytest.raises(panel.SourceConfigError, match="already a source of its own"):
        panel.set_custom_feeds(
            [{"name": "Again", "url": known.feed_url, "market": "AU"}], changed_by=ADMIN)


def test_checking_a_feed_says_what_came_back(panel):
    with patch("requests.get", return_value=MagicMock(status_code=200, text=RSS)):
        result = panel.check_feed("https://example.com/feed")
    assert result["ok"] is True and "1 article" in result["detail"]

    with patch("requests.get", return_value=MagicMock(status_code=403, text="")):
        result = panel.check_feed("https://example.com/feed")
    assert result["ok"] is False and "refuses automated readers" in result["detail"]

    with patch("requests.get", return_value=MagicMock(status_code=200, text="<html></html>")):
        result = panel.check_feed("https://example.com/feed")
    assert result["ok"] is False and "not a feed" in result["detail"]

    with pytest.raises(panel.SourceConfigError, match="full feed address"):
        panel.check_feed("example.com/feed")


# ---------- the settings are cached, and a write clears the cache ----------


def test_a_write_is_seen_straight_away(panel):
    assert panel.asx_tickers() == ()          # cached as empty
    panel.set_asx_tickers("BHP", changed_by=ADMIN)
    assert panel.asx_tickers() == ("BHP",)    # not the cached answer


# ---------- endpoints ----------


def _client(monkeypatch, *, auth_disabled: bool) -> TestClient:
    monkeypatch.setattr("api.auth.settings",
                        dataclasses.replace(real_settings, auth_disabled=auth_disabled))
    from api.server import app

    return TestClient(app)


@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/source-config"),
    ("put", "/api/admin/source-config/apify/token"),
    ("delete", "/api/admin/source-config/apify/token"),
    ("post", "/api/admin/source-config/apify/test"),
    ("put", f"/api/admin/source-config/apify/boards/{BOARD}"),
    ("put", "/api/admin/source-config/apify/run"),
    ("put", "/api/admin/source-config/asx"),
    ("put", "/api/admin/source-config/feeds"),
    ("post", "/api/admin/source-config/feeds/check"),
])
def test_only_administrators_reach_these(panel, monkeypatch, method, path):
    anon = _client(monkeypatch, auth_disabled=False)
    kw = {} if method in ("get", "delete") else {"json": {"token": TOKEN, "url": "https://a.example/f"}}
    assert getattr(anon, method)(path, **kw).status_code == 401
    monkeypatch.setattr("api.auth.current_user",
                        lambda request: {"email": "member@easyskill.com", "name": "Member"})
    assert getattr(anon, method)(path, **kw).status_code == 403
    assert panel.apify_token() == ""


def test_the_panel_flow(panel, monkeypatch):
    admin = _client(monkeypatch, auth_disabled=True)
    base = "/api/admin/source-config"

    r = admin.get(base)
    assert r.status_code == 200 and r.json()["apify"]["readyCount"] == 0
    assert admin.post(f"{base}/apify/test").status_code == 400, "nothing to test yet"

    # A board named before the token: saved, and the note says what is missing.
    r = admin.put(f"{base}/apify/boards/{BOARD}",
                  json={"actor": "someone/reader", "input": '{"q": "mining"}'})
    assert r.status_code == 200 and "Add an Apify token" in r.json()["note"]

    r = admin.put(f"{base}/apify/token", json={"token": TOKEN})
    assert r.status_code == 200 and TOKEN not in r.text
    assert r.json()["apify"]["readyCount"] == 1

    fine = MagicMock(status_code=200, json=lambda: {"data": {"username": "easyskill"}})
    with patch("requests.get", return_value=fine):
        r = admin.post(f"{base}/apify/test")
    assert r.json()["testOk"] is True and TOKEN not in r.text

    assert admin.put(f"{base}/apify/boards/{BOARD}",
                     json={"actor": "nonsense"}).status_code == 400
    assert admin.put(f"{base}/apify/boards/pngworkforce", json={"actor": "a/b"}).status_code == 400
    assert admin.put(f"{base}/apify/token", json={"token": "x"}).status_code == 400

    r = admin.put(f"{base}/apify/run", json={"maxChargeUsd": "0.75"})
    assert r.status_code == 200 and r.json()["apify"]["run"]["maxChargeUsd"] == 0.75
    assert "$0.75" in r.json()["note"]
    assert admin.put(f"{base}/apify/run", json={"maxChargeUsd": "999"}).status_code == 400
    r = admin.put(f"{base}/apify/run", json={"maxChargeUsd": ""})
    assert r.json()["apify"]["run"]["custom"] is False and "default" in r.json()["note"]

    r = admin.put(f"{base}/asx", json={"tickers": "bhp rio"})
    assert r.json()["asx"]["tickers"] == ["BHP", "RIO"] and "2 companies" in r.json()["note"]
    assert admin.put(f"{base}/asx", json={"tickers": "not a code!"}).status_code == 400
    assert admin.put(f"{base}/asx", json={"tickers": ""}).json()["asx"]["custom"] is False

    feed = {"name": "Paper", "url": "https://paper.example/feed", "market": "AU"}
    r = admin.put(f"{base}/feeds", json={"feeds": [feed]})
    assert r.status_code == 200 and r.json()["feeds"]["feeds"] == [feed]
    assert admin.put(f"{base}/feeds", json={"feeds": [{"name": "X"}]}).status_code == 400

    with patch("requests.get", return_value=MagicMock(status_code=200, text=RSS)):
        r = admin.post(f"{base}/feeds/check", json={"url": "https://paper.example/feed"})
    assert r.json()["ok"] is True
    assert admin.post(f"{base}/feeds/check", json={"url": "nope"}).status_code == 400

    r = admin.delete(f"{base}/apify/token")
    assert r.json()["apify"]["token"]["source"] == "none"
    assert r.json()["apify"]["readyCount"] == 0


def test_the_source_list_follows_the_panel(panel, monkeypatch):
    """Data sources shows a board as not configured until the panel has what it
    needs, and says where to go."""
    admin = _client(monkeypatch, auth_disabled=True)

    def row(name):
        body = admin.get("/api/admin/sources").json()
        return next(s for s in body["sources"] if s["name"] == name)

    before = row(BOARD)
    assert before["status"] == "not_configured" and "Integrations" in before["note"]
    assert before["setup"] == "apify", "the page links to where it is set up"
    assert row("newsfeed")["setup"] == "feeds"
    assert row("pngworkforce")["setup"] is None, "a source that is set up needs no link"

    panel.set_apify_token(TOKEN, changed_by=ADMIN)
    panel.set_board(BOARD, "someone/reader", "", changed_by=ADMIN)
    after = row(BOARD)
    assert after["status"] != "not_configured" and after["enabled"] is True
    assert after["setup"] is None, "set up, so it sits in the main table"
