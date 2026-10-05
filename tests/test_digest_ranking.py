"""How a digest ranks its signals (delivery.ranking), and what the page gets.

Pinned: a company's job ads fold into one line and nothing else does; a line's
score is the sum of three stated parts; a client outranks a stranger with
bigger news of the same kind; no company fills the page; each market keeps a
share; ties go to the more recent, never to the longer advert; the page and the
Slack message rank the same way; and the number the page used to call "conf"
is gone.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.digest_service import MAX_SIGNALS_SHOWN, build_digest_payload
from config.settings import settings as real_settings
from delivery import ranking
from delivery.digest import build_digest
from delivery.ranking import Item, rank
from loader.db import connect
from loader.ingest import init_db

NOW = datetime.now(timezone.utc)


def item(key, company="Acme", *, region="AU", category="hiring_velocity", kind="job_board",
         tier=None, is_new=False, minutes_ago=0, title=None) -> Item:
    return Item(key=key, company=company, region=region, category=category, kind=kind, tier=tier,
                is_new=is_new,
                captured_at=(NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds"),
                title=title or f"Role {key}")


# ---------- 1. folding ----------


def test_a_companys_job_ads_become_one_line():
    lines = rank([item(f"j{i}", "BHP", tier="A") for i in range(12)], 40)
    assert len(lines) == 1
    assert lines[0].count == 12 and lines[0].folded


def test_job_ads_in_different_markets_stay_separate():
    lines = rank([item("a", "BHP", region="AU"), item("b", "BHP", region="AU"),
                  item("c", "BHP", region="PNG")], 40)
    assert sorted((ln.lead.region, ln.count) for ln in lines) == [("AU", 2), ("PNG", 1)]


def test_news_and_tenders_are_never_folded():
    """Each is its own story; folding them would hide a second fact behind the first."""
    lines = rank([item("n1", "BHP", kind="news", category="project"),
                  item("n2", "BHP", kind="news", category="project"),
                  item("t1", "Defence", kind="tender", category="project"),
                  item("t2", "Defence", kind="tender", category="project")], 40)
    assert len(lines) == 4 and not any(ln.folded for ln in lines)


def test_ads_with_no_named_employer_are_not_folded_together():
    lines = rank([item("x", ""), item("y", "")], 40)
    assert len(lines) == 2


def test_the_newest_ad_leads_a_folded_line():
    lines = rank([item("old", "BHP", minutes_ago=90), item("new", "BHP", minutes_ago=1)], 40)
    assert lines[0].lead.key == "new"


def test_a_folded_line_is_said_in_a_sentence():
    line = rank([item(f"j{i}", "BHP", title=t) for i, t in
                 enumerate(["Diesel Fitter", "Belt Splicer", "Driller", "Surveyor", "Rigger"])], 40)[0]
    said = ranking.roles_summary(line)
    assert said.endswith("and 2 more.") and said.count(",") == 2


# ---------- 2. the score ----------


def test_the_score_is_the_sum_of_its_three_parts():
    line = rank([item("a", "BHP", tier="A", category="leadership", kind="news")], 40)[0]
    assert [p for _, p in line.parts] == [50, 30, 0]
    assert line.score == 80 and line.score == sum(p for _, p in line.parts)
    assert line.parts[0][0] == "Tier A client"


@pytest.mark.parametrize("tier,is_new,company,points", [
    ("A", False, "BHP", 50), ("B", False, "Santos", 40), ("C", False, "NRW", 30),
    (None, True, "Liontown", 18), (None, False, "Some Agency", 8), (None, True, "", 0),
])
def test_who_it_is_about(tier, is_new, company, points):
    assert ranking.standing(tier, is_new, company)[1] == points


@pytest.mark.parametrize("n,points", [(1, 0), (2, 6), (3, 11), (4, 11), (5, 16), (9, 16), (10, 20), (40, 20)])
def test_more_signals_about_a_company_add_to_each_of_them(n, points):
    assert ranking.activity(n) == points


def test_a_company_with_a_lot_going_on_scores_higher_for_it():
    busy = [item(f"j{i}", "BHP", tier="A") for i in range(10)]
    busy.append(item("news", "BHP", tier="A", kind="news", category="project"))
    quiet = [item("q", "Rio Tinto", tier="A", kind="news", category="project")]
    lines = {ln.lead.key: ln for ln in rank(busy + quiet, 40)}
    assert lines["news"].score == 50 + 26 + 20, "eleven signals about BHP in this digest"
    assert lines["q"].score == 50 + 26


def test_a_client_hiring_outranks_a_strangers_project_news():
    """The old order was category first, so this was the other way round."""
    lines = rank([item("stranger", "Nobody Mining", kind="news", category="project", is_new=True),
                  item("client", "BHP", tier="A")], 40)
    assert [ln.lead.key for ln in lines] == ["client", "stranger"]


def test_the_top_score_is_a_hundred():
    assert ranking.rules()["max"] == 100


# ---------- 3. selection ----------


def test_no_company_takes_more_than_its_lines():
    big = [item(f"b{i}", "BHP", tier="A", kind="news", category="leadership") for i in range(8)]
    rest = [item(f"r{i}", f"Co {i}", tier="C") for i in range(10)]
    chosen = rank(big + rest, 10)
    assert sum(1 for ln in chosen if ln.lead.company == "BHP") == ranking.MAX_PER_COMPANY
    assert len(chosen) == 10


def test_the_limit_gives_way_before_a_digest_goes_short():
    chosen = rank([item(f"b{i}", "BHP", tier="A", kind="news", category="project")
                   for i in range(8)], 6)
    assert len(chosen) == 6, "one company is all there is, so it fills the page"


def test_each_market_keeps_a_share():
    au = [item(f"au{i}", f"AU Co {i}", tier="A", kind="news", category="leadership")
          for i in range(30)]
    png = [item(f"png{i}", f"PNG Co {i}", region="PNG") for i in range(30)]
    chosen = rank(au + png, 10)
    regions = [ln.lead.region for ln in chosen]
    assert regions.count("PNG") == 3, "30% of ten, though every AU line outscores them"
    assert regions.count("AU") == 7


def test_a_market_with_little_to_show_does_not_hold_empty_rows():
    chosen = rank([item(f"au{i}", f"AU Co {i}") for i in range(20)]
                  + [item("png", "PNG Co", region="PNG")], 10)
    assert [ln.lead.region for ln in chosen].count("PNG") == 1 and len(chosen) == 10


def test_lines_come_back_strongest_first():
    chosen = rank([item("c", "C Co", tier="C"), item("a", "A Co", tier="A"),
                   item("b", "B Co", tier="B")], 10)
    assert [ln.lead.key for ln in chosen] == ["a", "b", "c"]
    assert [ln.score for ln in chosen] == sorted((ln.score for ln in chosen), reverse=True)


def test_a_tie_goes_to_the_more_recent_and_is_then_fixed():
    chosen = rank([item("older", "One", minutes_ago=60), item("newer", "Two", minutes_ago=1)], 10)
    assert [ln.lead.key for ln in chosen] == ["newer", "older"]
    same = rank([item("b", "Two"), item("a", "One")], 10)
    assert [ln.lead.key for ln in same] == ["a", "b"], "identical in every way: a fixed order"


# ---------- the page ----------


@pytest.fixture
def db(tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    path = tmp_path / "ranking.db"
    init_db(path, watchlist_path=wl)
    return path


def add(db, sid, *, company="BHP", tier="A", kind="job_board", category="hiring_velocity",
        geography="AU", title=None, is_new=0):
    captured = (NOW - timedelta(hours=2)).isoformat(timespec="seconds")
    raw = f"{title or 'Maintenance Planner'} | {company} | Newman WA | FIFO roster, start soon."
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, captured_at, "
            "geography, region, sector, company_name, watchlist_tier, signal_category, review_cycle, "
            "raw_content, analysis_notes, is_new_prospect, classified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, kind, "seek", f"https://x/{sid}", captured, geography, geography, "mining",
             company, tier, category, "weekly", raw, "the AI's note", is_new, captured))


def test_the_page_gets_one_row_for_a_companys_job_ads(db):
    for i, title in enumerate(["Diesel Fitter", "Belt Splicer", "Driller", "Surveyor"]):
        add(db, f"j{i}", title=title)
    add(db, "news", kind="news", category="project", title="BHP approves expansion")

    rows = build_digest_payload(db, days=7)["signals"]
    assert len(rows) == 2
    folded = next(r for r in rows if r.get("count"))
    assert folded["count"] == 4 and folded["title"] == "4 roles advertised"
    assert {r["title"] for r in folded["roles"]} == {"Diesel Fitter", "Belt Splicer", "Driller", "Surveyor"}
    assert all(r["desc"] and r["sourceUrl"] for r in folded["roles"]), "each role opens to its own text"
    assert "and 1 more." in folded["action"]

    news = next(r for r in rows if not r.get("count"))
    assert "roles" not in news and news["action"] == "the AI's note"


def test_every_row_carries_its_score_and_the_working(db):
    add(db, "j0")
    add(db, "news", kind="news", category="leadership", title="BHP names new COO")
    rows = build_digest_payload(db, days=7)["signals"]
    assert [r["n"] for r in rows] == ["01", "02"]
    assert rows[0]["id"] == "news" and rows[0]["score"] == 50 + 30 + 6
    for r in rows:
        assert r["score"] == sum(p["points"] for p in r["scoreParts"])
        assert "conf" not in r, "the number that measured nothing is gone"
        assert not [k for k in r if k.startswith("_")], "internal keys must not reach the browser"


def test_the_collection_counts_still_count_signals_not_rows(db):
    for i in range(5):
        add(db, f"j{i}")
    p = build_digest_payload(db, days=7)
    assert len(p["signals"]) == 1 and p["collection"]["shown"] == 1
    assert p["collection"]["jobs"] == 5, "five ads were collected, whatever they fold into"
    assert next(v for v in p["velocity"] if v["co"] == "BHP")["wk"] == 5


def test_a_new_name_is_described_by_its_headline_not_the_collected_text(db):
    add(db, "p", company="Liontown", tier=None, is_new=1, kind="news", category="project",
        title="Liontown approves Kathleen Valley expansion")
    name = build_digest_payload(db, days=7)["newNames"][0]
    assert name["signal"] == "Liontown approves Kathleen Valley expansion"


def test_a_new_names_headline_is_whole_and_a_very_long_one_ends_at_a_word(db):
    """It was cut at 90 characters mid-word, in a panel with room for all of it."""
    long = ("What does the Kathleen Valley expansion say about lithium demand and the "
            "contractors who will be hired to build it")
    add(db, "p1", company="Liontown", tier=None, is_new=1, kind="news", category="project", title=long)
    add(db, "p2", company="Wordy Co", tier=None, is_new=1, kind="news", category="project",
        title="mining " * 60)
    names = {n["co"]: n for n in build_digest_payload(db, days=7)["newNames"]}
    assert names["Liontown"]["signal"] == long and len(long) > 90
    cut = names["Wordy Co"]["signal"]
    assert cut.endswith("mining…") and len(cut) <= 201
    assert set(names["Liontown"]) == {"co", "signal", "sector", "region"}, "no tier to add it to"


def test_the_page_never_shows_more_than_its_limit(db):
    for i in range(MAX_SIGNALS_SHOWN + 15):
        add(db, f"s{i}", company=f"Employer {i}", tier=None, is_new=1)
    assert len(build_digest_payload(db, days=7)["signals"]) == MAX_SIGNALS_SHOWN


# ---------- Slack ranks the same way ----------


def test_the_slack_digest_folds_and_ranks_like_the_page(db):
    for i, title in enumerate(["Diesel Fitter", "Belt Splicer", "Driller"]):
        add(db, f"j{i}", title=title)
    add(db, "stranger", company="Nobody Mining", tier=None, is_new=1, kind="news",
        category="project", title="Nobody Mining starts drilling")
    add(db, "png", company="Newmont", tier="A", geography="PNG", title="Process Operator")

    text = build_digest(db, since=NOW - timedelta(days=7))
    key = text.split(":bar_chart:")[0]
    assert "*BHP* _(Tier A)_ — 3 roles advertised:" in key
    assert key.count("*BHP*") == 1, "three ads, one line"
    assert key.index("*BHP*") < key.index("*Nobody Mining*"), "the client leads the stranger"
    assert "PAPUA NEW GUINEA" in key and "*Newmont*" in key


# ---------- the rules, for the page guide ----------


def test_the_guide_reads_the_rules_from_the_code(monkeypatch):
    monkeypatch.setattr("api.auth.settings",
                        dataclasses.replace(real_settings, auth_disabled=True))
    from api.server import app

    r = TestClient(app).get("/api/digest/ranking")
    assert r.status_code == 200, "not mistaken for a run called 'ranking'"
    body = r.json()
    assert body["limit"] == MAX_SIGNALS_SHOWN and body["max"] == 100
    assert {"label": "Tier A client", "points": ranking.TIER_POINTS["A"]} in body["who"]
    assert {"label": "Leadership change", "points": 30} in body["what"]
    assert body["perCompany"] == ranking.MAX_PER_COMPANY and body["regionShare"] == 30
    # Fewest signals first, the way the guide lists them.
    assert [m["points"] for m in body["more"]] == sorted(m["points"] for m in body["more"])


def test_the_rules_are_not_public(monkeypatch):
    monkeypatch.setattr("api.auth.settings",
                        dataclasses.replace(real_settings, auth_disabled=False))
    from api.server import app

    assert TestClient(app).get("/api/digest/ranking").status_code == 401
