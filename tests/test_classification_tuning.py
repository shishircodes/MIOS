"""Classification tuning, from the audit of production on 5 Oct 2026.

Read against 1,487 classified records and a sample of 79 read by eye:

1. Agencies whose names carry no tell-tale word, and governments named in news
   as the owner of a project, were still offered as new prospects.
2. The review cycle the model returned was the category said twice.
3. A third of job ads read by the model were irrelevant, some of them
   recognisable from the title alone, and one tender source returned nothing
   relevant at all.
4. About 70% of each call's output was the model reasoning to itself.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.prompts import SYSTEM_PROMPT
from agents.prospects import is_agency, is_government, is_prospect
from agents.signal_analyst import (
    BATCH_RESPONSE_SCHEMA, CYCLE_FOR_CATEGORY, _build_batch_prompt, classify_pending, prefilter,
)
from loader import pipeline_settings
from loader.ingest import init_db
from scraper import catalog


@pytest.fixture
def db(tmp_path: Path) -> Path:
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    path = tmp_path / "tuning.db"
    init_db(path, watchlist_path=wl)
    return path


def _row(db: Path, content: str, *, source_type: str = "news", source_name: str = "newsfeed") -> str:
    sid = uuid.uuid4().hex
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, captured_at, "
            "geography, raw_content) VALUES (?, ?, ?, ?, ?, 'AU', ?)",
            (sid, source_type, source_name, f"https://example.com/{sid}",
             datetime.now(timezone.utc).isoformat(timespec="seconds"), content))
    return sid


def _answers(*items):
    """A model that returns these classifications, in order."""
    queue = list(items)

    def _call(_sys, user_prompt, schema=None, **_kw):
        n = user_prompt.count("--- SIGNAL ")
        out, queue[:] = queue[:n], queue[n:]
        return out
    return _call


def _stored(db: Path, sid: str) -> dict:
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        return dict(c.execute("SELECT * FROM signals WHERE signal_id = ?", (sid,)).fetchone())


# ---------- 1. who is not a prospect ----------


@pytest.mark.parametrize("name", [
    "Mining People International", "WorkPac", "WorkPac - Mining Qld", "Programmed",
    "Zenith Search", "Chandler Macleod Group",
])
def test_agencies_named_in_production_are_recognised(name):
    """None of these names carries a word like "recruitment"."""
    assert is_agency(name)
    assert not is_prospect(name, "mining", "job_board", watchlisted=False)


@pytest.mark.parametrize("name", [
    "Victorian Government", "New South Wales Government", "Department of Works and Highways",
    "Frankston City Council", "High Speed Rail Authority", "Internal Revenue Commission",
    "World Bank", "Australian Defence Force",
])
def test_a_government_body_is_not_a_prospect(name):
    """It owns or funds the work; the contractor who wins it does the hiring."""
    assert is_government(name)
    assert not is_prospect(name, "construction", "news", watchlisted=False)


@pytest.mark.parametrize("name", ["Inland Rail", "Marinus Link", "Snowy Hydro", "Oil Search",
                                  "Liontown", "Hudson Resources", "Councillor Mining Services"])
def test_companies_are_still_prospects(name):
    """Government-owned companies that employ their own crews carry none of the
    words, and a word inside another word does not count."""
    assert not is_government(name) and not is_agency(name)
    assert is_prospect(name, "mining", "news", watchlisted=False)


def test_an_unnamed_advertiser_is_nobody_to_approach():
    assert not is_prospect("Private Advertiser", "construction", "job_board", watchlisted=False)


def test_the_rule_is_applied_to_what_the_model_returns(db):
    gov = _row(db, "New works to clean the Monash Freeway | Roads & Infrastructure | The Victorian "
                   "Government is rolling out works.")
    co = _row(db, "Inland Rail track laying begins | Infrastructure Magazine | First section laid.")
    answer = {"sector": "construction", "signal_category": "project", "watchlist_match": None,
              "is_new_prospect": True, "reasoning": "x"}
    classify_pending(db, gemini_caller=_answers(
        {**answer, "company_name": "Victorian Government"}, {**answer, "company_name": "Inland Rail"}))
    assert _stored(db, gov)["is_new_prospect"] == 0
    assert _stored(db, co)["is_new_prospect"] == 1


# ---------- 2. the review cycle follows from the category ----------


def test_every_category_has_a_cycle():
    from agents.signal_analyst import ALLOWED_CATEGORIES, ALLOWED_CYCLES

    assert set(CYCLE_FOR_CATEGORY) == set(ALLOWED_CATEGORIES)
    assert set(CYCLE_FOR_CATEGORY.values()) <= ALLOWED_CYCLES


def test_the_model_is_no_longer_asked_for_a_cycle():
    assert "review_cycle" not in json.dumps(BATCH_RESPONSE_SCHEMA)
    assert "review_cycle" not in SYSTEM_PROMPT and "REVIEW CYCLES" not in SYSTEM_PROMPT
    assert "review" not in _build_batch_prompt([("a", "Driller | BHP | Perth")], "BHP").lower()


@pytest.mark.parametrize("category,cycle", sorted(CYCLE_FOR_CATEGORY.items()))
def test_the_stored_cycle_is_the_categorys(db, category, cycle):
    sid = _row(db, "Liontown approves Kathleen Valley expansion | Mining Monthly | Lithium thaws.")
    classify_pending(db, gemini_caller=_answers({
        "company_name": "Liontown", "sector": "mining", "signal_category": category,
        "watchlist_match": None, "is_new_prospect": False, "reasoning": "x",
        # A model that still sends one, or sends the wrong one, changes nothing.
        "review_cycle": "weekly" if cycle != "weekly" else "quarterly",
    }))
    assert _stored(db, sid)["review_cycle"] == cycle


# ---------- 3. less that is irrelevant reaches the model ----------

PAD = " | Some Employer Ltd | Port Moresby | A description long enough to pass the length check."


@pytest.mark.parametrize("title", [
    "Lecturer - Agriculture Finance & Investments", "Associate Professor in Mathematics",
    "Marketing Officer", "Sales Representative (Daikin PNG)", "Virtual Receptionist",
    "Lead Counsellor", "Credit Controller", "Senior Manager - Consumer Lending",
    "Head of Business Banking (SME) - PNG", "Midwife Supervisor", "Assistant Pharmacist",
    "Dental Assistant - NO EXPERIENCE REQUIRED",
])
def test_titles_that_are_never_easy_skill_roles_stop_before_the_model(title):
    assert prefilter(title + PAD, "job_board") == (False, "blocklist")


@pytest.mark.parametrize("title", [
    "Occupational Health Nurse", "Camp Chef", "Inventory Controller", "Technical Instructor",
    "Site Administrator", "Accounts Payable Officer", "Mine Surveyor", "Diesel Fitter",
])
def test_titles_that_can_be_site_roles_still_reach_the_model(title):
    """Left alone on purpose: each can be a role on a mine or a camp."""
    assert prefilter(title + PAD, "job_board") == (True, None)


def test_the_new_words_do_not_touch_news_or_tenders():
    text = "Banking royal commission into mining lending | Mining.com.au | Lenders questioned."
    assert prefilter(text, "news") == (True, None)


def test_eu_tenders_ship_switched_off_with_the_reason():
    from loader.source_settings import default_enabled

    ted = catalog.get("ted")
    assert ted.collectable, "still there to switch on"
    assert default_enabled("ted") is False
    assert "aid programme" in (ted.off_reason or "")


# ---------- 4. the model's hidden reasoning ----------


def test_reasoning_is_off_by_default_and_an_administrator_can_give_it_back(db):
    assert pipeline_settings.classifier(db).thinking_tokens == 0
    spec = next(s for s in pipeline_settings.CLASSIFIER_SPECS if s.key == "classify.thinking_tokens")
    assert (spec.default, spec.low) == (0, 0) and spec.high >= 8000


@pytest.mark.parametrize("model,wanted,sent", [
    ("gemini-2.5-flash", 0, 0),                 # off
    ("gemini-2.5-flash", 2000, 2000),
    ("gemini-2.5-flash-lite", 0, 0),
    ("gemini-2.5-pro", 0, 128),                 # cannot be switched off, and refuses less
    ("gemini-2.5-pro", 4000, 4000),
    ("gemini-2.0-flash", 0, None),              # has no thinking to configure
    ("some-future-model", 0, None),             # nothing assumed
    ("gemini-2.5-flash", None, None),           # nobody asked
])
def test_each_gemini_model_gets_a_budget_it_accepts(model, wanted, sent):
    """A budget a model refuses fails the whole call, so it is never sent."""
    from llm.providers import gemini_thinking_budget

    assert gemini_thinking_budget(model, wanted) == sent


def test_classification_asks_for_the_administrators_budget(db, monkeypatch):
    import llm

    seen = {}

    def fake_caller_for(purpose, *, thinking_budget=None):
        seen.update(purpose=purpose, thinking_budget=thinking_budget)
        return _answers()

    monkeypatch.setattr(llm, "caller_for", fake_caller_for)
    classify_pending(db)
    assert seen == {"purpose": llm.PURPOSE_CLASSIFY, "thinking_budget": 0}

    pipeline_settings.update({"classify.thinking_tokens": 1500}, changed_by="admin@example.com",
                             target=db)
    classify_pending(db)
    assert seen["thinking_budget"] == 1500


def test_other_jobs_keep_the_models_own_default(monkeypatch):
    """Only classification sets a budget; the Market Pulse is prose and keeps
    whatever the model does by default."""
    import llm.providers as providers

    built = {}

    class Fake:
        label = "Fake"

        def build(self, model, **kwargs):
            built.update(model=model, kwargs=kwargs)
            return lambda *a, **k: "ok"

    monkeypatch.setitem(providers._PROVIDERS, "gemini", Fake())
    monkeypatch.setattr(providers, "resolve", lambda purpose, stored=None: ("gemini", "gemini-2.5-flash"))
    providers.caller_for("pulse")
    assert built["kwargs"] == {}
    providers.caller_for("classify", thinking_budget=0)
    assert built["kwargs"] == {"thinking_budget": 0}
