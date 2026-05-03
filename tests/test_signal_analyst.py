"""Tests for agents.signal_analyst. Gemini is mocked throughout."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agents.signal_analyst import (
    classify_pending,
    fuzzy_match_watchlist,
    prefilter,
    _coerce_classification,
)
from loader.ingest import init_db, ingest


@pytest.fixture
def watchlist_file(tmp_path: Path) -> Path:
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining",
         "notes": "", "aliases": ["BHP Group", "BHP Billiton"]},
        {"company_name": "Newmont", "tier": "A", "sector": "mining",
         "notes": "", "aliases": ["Newmont Mining", "Newmont Lihir", "Newcrest Lihir"]},
        {"company_name": "Technip", "tier": "B", "sector": "oil_gas",
         "notes": "", "aliases": ["TechnipFMC", "TechnipEnergies"]},
    ]))
    return p


@pytest.fixture
def db(tmp_path: Path, watchlist_file: Path) -> Path:
    p = tmp_path / "test.db"
    init_db(p, watchlist_path=watchlist_file)
    return p


# ---------- prefilter ----------

def test_prefilter_too_short():
    ok, reason = prefilter("short text")
    assert ok is False and reason == "too_short"


def test_prefilter_blocklist():
    ok, reason = prefilter(
        "Marketing Manager - lead consumer marketing for our flagship juice brand at Sunshine."
    )
    assert ok is False and reason == "blocklist"


def test_prefilter_passes():
    ok, reason = prefilter(
        "Maintenance Planner - BHP Pilbara. Strong CMMS / Maximo experience required."
    )
    assert ok is True and reason is None


# ---------- fuzzy watchlist ----------

def test_fuzzy_match_exact(db, watchlist_file):
    with sqlite3.connect(db) as c:
        from agents.signal_analyst import _load_watchlist
        wl = _load_watchlist(c)
    name, tier = fuzzy_match_watchlist("BHP", wl)
    assert name == "BHP" and tier == "A"


def test_fuzzy_match_alias(db):
    with sqlite3.connect(db) as c:
        from agents.signal_analyst import _load_watchlist
        wl = _load_watchlist(c)
    name, tier = fuzzy_match_watchlist("Newcrest Lihir", wl)
    assert name == "Newmont" and tier == "A"


def test_fuzzy_match_typo(db):
    with sqlite3.connect(db) as c:
        from agents.signal_analyst import _load_watchlist
        wl = _load_watchlist(c)
    name, _ = fuzzy_match_watchlist("TechnipFMC Australia", wl)
    assert name == "Technip"


def test_fuzzy_match_unknown(db):
    with sqlite3.connect(db) as c:
        from agents.signal_analyst import _load_watchlist
        wl = _load_watchlist(c)
    name, tier = fuzzy_match_watchlist("Some Random Co", wl)
    assert name is None and tier is None


def test_fuzzy_match_none_input(db):
    with sqlite3.connect(db) as c:
        from agents.signal_analyst import _load_watchlist
        wl = _load_watchlist(c)
    assert fuzzy_match_watchlist(None, wl) == (None, None)
    assert fuzzy_match_watchlist("", wl) == (None, None)


# ---------- coerce ----------

def test_coerce_invalid_enums_falls_back():
    out = _coerce_classification({
        "company_name": "BHP",
        "sector": "spaceships",
        "signal_category": "vibes",
        "review_cycle": "annually",
        "watchlist_match": "BHP",
        "is_new_prospect": False,
        "reasoning": "x",
    })
    assert out["sector"] == "other"
    assert out["signal_category"] == "hiring_velocity"
    assert out["review_cycle"] == "weekly"


# ---------- classify_pending end-to-end (Gemini mocked) ----------

def _fake_caller_factory(payload: dict):
    def _call(_sys, _user):
        return payload
    return _call


def test_classify_pending_happy_path(db):
    ingest(
        [
            {"source_url": "u1", "raw_content":
             "Maintenance Planner role at BHP Pilbara, FIFO 8/6 ex-Perth, multiple roles open."}
        ],
        db,
    )
    fake = _fake_caller_factory({
        "company_name": "BHP",
        "sector": "mining",
        "signal_category": "hiring_velocity",
        "review_cycle": "weekly",
        "watchlist_match": "BHP",
        "is_new_prospect": False,
        "reasoning": "BHP Pilbara hiring",
    })
    counts = classify_pending(db, gemini_caller=fake)
    assert counts["classified"] == 1
    assert counts.get("hiring_velocity") == 1

    with sqlite3.connect(db) as c:
        row = c.execute(
            "SELECT company_name, sector, signal_category, review_cycle, watchlist_tier, "
            "classified_at FROM signals"
        ).fetchone()
    assert row[0] == "BHP"
    assert row[1] == "mining"
    assert row[2] == "hiring_velocity"
    assert row[3] == "weekly"
    assert row[4] == "A"
    assert row[5] is not None


def test_classify_pending_prefilter_short(db):
    ingest([{"source_url": "u-short", "raw_content": "tiny"}], db)
    # raw_content="tiny" gets stripped by ingest? It's < MIN but not empty,
    # so it does insert (>= 1 char). Will be filtered by classifier.
    fake = _fake_caller_factory({})  # should never be called
    counts = classify_pending(db, gemini_caller=fake)
    assert counts.get("filtered_too_short") == 1
    assert counts.get("classified", 0) == 0


def test_classify_pending_blocklist(db):
    ingest(
        [{"source_url": "u-mkt", "raw_content":
          "Marketing Manager - lead consumer marketing for our flagship juice brand. 7 yrs FMCG."}],
        db,
    )
    fake = _fake_caller_factory({})
    counts = classify_pending(db, gemini_caller=fake)
    assert counts.get("filtered_blocklist") == 1


def test_classify_pending_alias_resolves_to_canonical(db):
    ingest(
        [{"source_url": "u-alias", "raw_content":
          "Senior Geologist at Newcrest Lihir Operations PNG, FIFO Cairns/POM rotation."}],
        db,
    )
    fake = _fake_caller_factory({
        "company_name": "Newcrest Lihir Operations",
        "sector": "mining",
        "signal_category": "hiring_velocity",
        "review_cycle": "weekly",
        "watchlist_match": "Newcrest Lihir",
        "is_new_prospect": False,
        "reasoning": "alias of Newmont",
    })
    classify_pending(db, gemini_caller=fake)
    with sqlite3.connect(db) as c:
        row = c.execute(
            "SELECT company_name, watchlist_tier, is_new_prospect FROM signals"
        ).fetchone()
    # company_name is the LLM's value; watchlist_tier comes from canonical resolution
    assert row[1] == "A"  # Newmont is tier A
    assert row[2] == 0


def test_classify_pending_new_prospect(db):
    ingest(
        [{"source_url": "u-new", "raw_content":
          "Reservoir Engineer at Kumul Petroleum, Port Moresby. PNG state-owned operator role."}],
        db,
    )
    fake = _fake_caller_factory({
        "company_name": "Kumul Petroleum",
        "sector": "oil_gas",
        "signal_category": "hiring_velocity",
        "review_cycle": "weekly",
        "watchlist_match": None,
        "is_new_prospect": True,
        "reasoning": "non-watchlist PNG operator",
    })
    classify_pending(db, gemini_caller=fake)
    with sqlite3.connect(db) as c:
        row = c.execute(
            "SELECT company_name, watchlist_tier, is_new_prospect FROM signals"
        ).fetchone()
    assert row[0] == "Kumul Petroleum"
    assert row[1] is None
    assert row[2] == 1


def test_classify_pending_handles_caller_exception(db):
    ingest(
        [{"source_url": "u-err", "raw_content":
          "Process Engineer at TotalEnergies Papua LNG, supporting Train 3 development team."}],
        db,
    )

    def _boom(_s, _u):
        raise RuntimeError("network down")

    counts = classify_pending(db, gemini_caller=_boom)
    assert counts.get("errors") == 1
    assert counts.get("classified", 0) == 0
    with sqlite3.connect(db) as c:
        classified_at = c.execute(
            "SELECT classified_at FROM signals"
        ).fetchone()[0]
    assert classified_at is None  # left pending so a re-run can retry
