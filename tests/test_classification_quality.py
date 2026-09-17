"""Classification quality: the four fixes from reviewing production output.

Found by comparing 920 classified rows against their raw text:

1. New prospects included airlines, banks, recruitment agencies and tender
   buyers, because the flag was the model's call and it ignored its prompt.
2. The blocklist searched the whole advert, dropping relevant work on a word in
   a company description.
3. Signals outside the five sectors were counted in every figure.
4. The prompt described every item as a job ad, though half the sources are
   news and tenders.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.prospects import is_agency, is_prospect
from agents.signal_analyst import _build_batch_prompt, classify_pending, prefilter
from loader.ingest import init_db

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    path = tmp_path / "quality.db"
    init_db(path, watchlist_path=wl)
    return path


def _row(db, *, content, source_type="job_board", source_name="seek", company=None,
         sector=None, category=None, tier=None, new=0, classified=True, days_ago=0,
         notes="auto", run_id=None):
    when = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    sid = uuid.uuid4().hex
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, captured_at, "
            "geography, region, sector, company_name, watchlist_tier, signal_category, review_cycle, "
            "raw_content, analysis_notes, is_new_prospect, classified_at, run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, source_type, source_name, f"https://x/{sid}", when, "AU", "AU", sector, company,
             tier, category, "weekly" if classified else None, content,
             notes if classified else None, new, when if classified else None, run_id),
        )
    return sid


# ---------- 1. prospects are decided by rule ----------


@pytest.mark.parametrize("company,sector,kind,watchlisted,expected", [
    ("Durack Civil", "construction", "job_board", False, True),
    ("Lindian Resources", "mining", "news", False, True),
    ("BHP", "mining", "job_board", True, False),            # already a client
    ("PNG Air", "other", "job_board", False, False),         # not our sectors
    ("Unknown", "mining", "job_board", False, False),        # nobody to approach
    (None, "mining", "news", False, False),
    ("Department of Defence - DSRG", "defence", "tender", False, False),  # a buyer
    ("Allstar Recruitment Group", "mining", "job_board", False, False),   # an agency
    ("Programmed Skilled Workforce", "construction", "job_board", False, False),
    ("PeopleConnexion", "mining", "job_board", False, False),  # named competitor
])
def test_who_counts_as_a_new_prospect(company, sector, kind, watchlisted, expected):
    assert is_prospect(company, sector, kind, watchlisted=watchlisted) is expected


def test_agency_words_match_whole_words_only():
    assert is_agency("2XM Recruit")
    assert is_agency("Construction Talent Authority")
    assert not is_agency("Recruitmentville Mining")  # contains, but is not, the word
    assert not is_agency("Fortescue")


def _fake_caller(item):
    def _call(_sys, user_prompt, schema=None, **_kw):
        n = user_prompt.count("--- SIGNAL ")
        return [dict(item) for _ in range(n)]
    return _call


def test_the_model_cannot_make_an_irrelevant_employer_a_prospect(db):
    sid = _row(db, content="Cabin Crew | PNG Air | Port Moresby | Join our airline team today.",
               classified=False)
    classify_pending(db, gemini_caller=_fake_caller({
        "company_name": "PNG Air", "sector": "other", "signal_category": "hiring_velocity",
        "review_cycle": "weekly", "watchlist_match": None,
        "is_new_prospect": True,   # what the model actually did
        "reasoning": "airline hiring",
    }))
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT is_new_prospect FROM signals WHERE signal_id=?",
                         (sid,)).fetchone()[0] == 0


def test_a_relevant_company_the_model_forgot_still_becomes_a_prospect(db):
    sid = _row(db, content="Site Supervisor | Durack Civil | Brisbane | Civil works across QLD.",
               classified=False)
    classify_pending(db, gemini_caller=_fake_caller({
        "company_name": "Durack Civil", "sector": "construction",
        "signal_category": "hiring_velocity", "review_cycle": "weekly",
        "watchlist_match": None, "is_new_prospect": False, "reasoning": "civil hiring",
    }))
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT is_new_prospect FROM signals WHERE signal_id=?",
                         (sid,)).fetchone()[0] == 1


def test_rematch_corrects_prospects_already_stored(db, monkeypatch):
    from loader.rematch import rematch

    agency = _row(db, content="Driller | Allstar Recruitment Group | Perth | FIFO roster.",
                  company="Allstar Recruitment Group", sector="mining", new=1)
    tender = _row(db, content="Fire services upgrade | Department of Defence | Building | closes",
                  source_type="tender", source_name="austender",
                  company="Department of Defence", sector="defence", new=1)
    fine = _row(db, content="Site Supervisor | Durack Civil | Brisbane | Civil works.",
                company="Durack Civil", sector="construction", new=1)

    summary = rematch(db)

    with sqlite3.connect(db) as c:
        flags = dict(c.execute("SELECT signal_id, is_new_prospect FROM signals").fetchall())
    assert (flags[agency], flags[tender], flags[fine]) == (0, 0, 1)
    assert summary["changed"] == 2


# ---------- 2. the blocklist reads the title ----------


def test_a_blocked_word_in_the_description_no_longer_drops_the_job():
    ok, _ = prefilter("Track Protection Officer | Wabtec | Perth | Trade & Construction | "
                      "We serve retail and rail customers nationally.", "job_board")
    assert ok


def test_a_blocked_word_in_the_employer_name_no_longer_drops_the_job():
    ok, _ = prefilter("Glazier | Ruks Real Estate Limited | Morobe | Cut and fit glass on site.",
                      "job_board")
    assert ok


def test_a_blocked_job_title_is_still_dropped():
    assert prefilter("Retail Branch Manager | CHM & Sons | NCD | Lead the store team.",
                     "job_board") == (False, "blocklist")


def test_news_and_tenders_are_never_blocklisted():
    story = "FCR Resources Day highlights investment | Mining.com.au | held at a hotel in Perth"
    assert prefilter(story, "news")[0]
    assert prefilter("Hotel refurbishment works | Defence Housing | Building | closes soon", "tender")[0]


def test_blocklist_words_match_whole_words():
    # "cafe" must not fire inside another word.
    assert prefilter("Cafeteria Refurbishment Supervisor | Downer | Perth | Site works.",
                     "job_board")[0]


def test_requeue_finds_rows_the_old_rule_dropped(db):
    from loader.requeue_prefiltered import requeue

    wrong = _row(db, content="Track Protection Officer | Wabtec | Perth | retail and rail",
                 sector="other", category="hiring_velocity", notes="prefiltered:blocklist")
    right = _row(db, content="Retail Branch Manager | CHM | NCD | Lead the store team today.",
                 sector="other", category="hiring_velocity", notes="prefiltered:blocklist")

    found = requeue(db)
    assert [r["signal_id"] for r in found] == [wrong], "dry run lists only the wrongly dropped"

    requeue(db, apply=True)
    with sqlite3.connect(db) as c:
        states = dict(c.execute("SELECT signal_id, classified_at FROM signals").fetchall())
    assert states[wrong] is None, "cleared for the next run"
    assert states[right] is not None


# ---------- 3. irrelevant signals are left out of the figures ----------


def test_the_digest_leaves_out_signals_outside_the_sectors(db):
    from api.digest_service import build_digest_payload

    _row(db, content="Drill Fitter | BHP | Newman | FIFO roster.", company="BHP",
         sector="mining", category="hiring_velocity", tier="A", run_id="r1")
    _row(db, content="Cabin Crew | PNG Air | Port Moresby | Airline.", company="PNG Air",
         sector="other", category="hiring_velocity", new=1, run_id="r1")

    p = build_digest_payload(db, run_id="r1")

    assert p["collection"]["collected"] == 1
    assert p["collection"]["notRelevant"] == 1
    assert [s["company"] for s in p["signals"]] == ["BHP"]
    assert all(n["co"] != "PNG Air" for n in p["newNames"])


def test_the_dashboard_leaves_them_out_and_counts_them(db):
    from api.dashboard_service import build_dashboard_payload

    _row(db, content="Drill Fitter | BHP | Newman | FIFO roster.", company="BHP",
         sector="mining", category="hiring_velocity", tier="A")
    _row(db, content="Cabin Crew | PNG Air | Port Moresby | Airline.", company="PNG Air",
         sector="other", category="market_intel", new=1)

    p = build_dashboard_payload(db)

    assert p["latest"]["total"] == 1
    assert p["notRelevant"] == 1
    assert "other" not in {s["key"] for s in p["sectors"]}
    assert all(c["name"] != "PNG Air" for c in p["companies"])
    assert p["newNames"] == 0


def test_the_slack_digest_leaves_them_out(db):
    from delivery.digest import build_digest

    _row(db, content="Cabin Crew | PNG Air | Port Moresby | Airline recruitment drive.",
         company="PNG Air", sector="other", category="hiring_velocity", new=1)

    text = build_digest(db, since=NOW - timedelta(days=7))
    assert "PNG Air" not in text


# ---------- 4. the model is told what each item is ----------


def test_each_item_is_labelled_with_its_kind():
    prompt = _build_batch_prompt(
        [("a", "Driller | BHP"), ("b", "Headline | Mining.com.au"), ("c", "Works | Defence")],
        "BHP", {"a": "job_board", "b": "news", "c": "tender"},
    )
    assert "--- SIGNAL 1 (job ad) ---" in prompt
    assert "--- SIGNAL 2 (news) ---" in prompt
    assert "--- SIGNAL 3 (tender) ---" in prompt


def test_the_system_prompt_covers_news_tenders_and_the_catch_all():
    from agents.prompts import SYSTEM_PROMPT

    assert "tender" in SYSTEM_PROMPT and "news" in SYSTEM_PROMPT
    assert "Never the publication" in SYSTEM_PROMPT
    assert "Do NOT use it as a" in SYSTEM_PROMPT
