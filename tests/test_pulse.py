"""Tests for Market Pulse (delivery.pulse).

Two decisions distinguish this from the Mode Publish rewrite, and both are load
bearing:

* **No fallback to computed prose.** A failed generation omits the section. The
  old template bullets ("Geographic split: AU 86 / PNG 60") must never appear in
  the place a written summary would go — they imply a judgement nobody made.
* **Interpretation is allowed, but labelled.** The model may reason past the
  figures. It may not invent figures, and it may not present a reading as a
  measurement.

Gemini is faked throughout. A test that depends on a live model and a daily
quota is not a test.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from delivery.digest import build_digest
from delivery.pulse import (
    KIND_FACT,
    KIND_INTERPRETATION,
    STATUS_FAILED,
    STATUS_GENERATED,
    build_evidence,
    generate_pulse,
    load_pulse,
    save_pulse,
)
from loader.db import connect
from loader.ingest import init_db


@pytest.fixture
def watchlist(tmp_path):
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    return p


@pytest.fixture
def db(tmp_path, watchlist):
    path = tmp_path / "pulse.db"
    init_db(path, watchlist_path=watchlist)
    return path


PAYLOAD = {
    "weekLabel": "week of 18 August 2026",
    "collection": {"collected": 146, "jobs": 116, "news": 30, "shown": 40,
                   "newNames": 61, "sources": 4, "regions": {"AU": 86, "PNG": 60}},
    "velocity": [
        {"co": "BHP", "wk": 18, "avg": 25.5, "change": -29, "basis": 2,
         "sector": "Mining", "tier": "A"},
        {"co": "Newmont", "wk": 3, "avg": 0.0, "change": None, "basis": 0,
         "sector": "Mining", "tier": "A"},
    ],
    "signals": [
        {"region": "AU", "company": "BHP", "sector": "Mining", "category": "hiring_velocity",
         "title": "Maintenance Planner, Newman", "action": "Cluster at one site."},
    ],
    "newNames": [{"co": "Hazer Group", "sector": "Mining", "region": "AU"}],
}


def _caller(bullets):
    def _call(_system, _user, _schema=None):
        return {"bullets": bullets}
    return _call


# ---------- what the model is shown ----------


def test_the_evidence_carries_the_measured_deltas(db):
    """The model must not have to guess at week-over-week movement — it is
    already computed, and a guess would be a fabrication."""
    ev = build_evidence(PAYLOAD)
    assert "down 29%" in ev
    assert "average of 25.5" in ev


def test_a_company_with_no_history_is_marked_as_having_none(db):
    """Otherwise the model reads a missing baseline as zero and reports a rise
    from nothing — the exact bug the velocity baseline was fixed for."""
    ev = build_evidence(PAYLOAD)
    assert "Newmont" in ev
    assert "no prior baseline" in ev


def test_the_evidence_is_the_figures_the_digest_publishes(db):
    ev = build_evidence(PAYLOAD)
    for figure in ("146", "116", "30", "86", "60", "61"):
        assert figure in ev


# ---------- interpretation is allowed ----------


def test_an_interpretation_bullet_survives(db):
    """The whole point of the decision: a read on the week that can only restate
    the week's arithmetic is not worth a model."""
    out = generate_pulse(PAYLOAD, target=db, gemini_caller=_caller([
        {"text": "The cluster of maintenance roles at Newman suggests shutdown preparation.",
         "kind": KIND_INTERPRETATION},
    ]))
    assert out.status == STATUS_GENERATED
    assert out.bullets[0]["kind"] == KIND_INTERPRETATION


def test_an_unlabelled_bullet_is_discarded(db):
    """Defaulting it to `fact` would launder a guess into a measurement."""
    out = generate_pulse(PAYLOAD, target=db, gemini_caller=_caller([
        {"text": "Mining is heating up.", "kind": ""},
        {"text": "146 signals were collected.", "kind": KIND_FACT},
    ]))
    assert [b["text"] for b in out.bullets] == ["146 signals were collected."]
    assert "unlabelled" in (out.note or "")


# ---------- but invented numbers are not ----------


def test_a_bullet_inventing_a_figure_is_discarded(db):
    """Reasoning past the evidence is permitted. Inventing evidence is not."""
    out = generate_pulse(PAYLOAD, target=db, gemini_caller=_caller([
        {"text": "Hiring is up 240% on last week.", "kind": KIND_FACT},
        {"text": "146 signals were collected from 4 sources.", "kind": KIND_FACT},
    ]))
    assert len(out.bullets) == 1
    assert "240" in (out.note or "")


def test_an_interpretation_may_not_invent_figures_either(db):
    """The label is not a licence to make numbers up."""
    out = generate_pulse(PAYLOAD, target=db, gemini_caller=_caller([
        {"text": "The 47% jump suggests shutdown preparation.", "kind": KIND_INTERPRETATION},
    ]))
    assert out.status == STATUS_FAILED
    assert out.bullets == []


def test_reusing_a_figure_from_the_evidence_is_fine(db):
    out = generate_pulse(PAYLOAD, target=db, gemini_caller=_caller([
        {"text": "BHP is down 29% against its baseline.", "kind": KIND_FACT},
    ]))
    assert len(out.bullets) == 1


def test_at_most_five_bullets(db):
    out = generate_pulse(PAYLOAD, target=db, gemini_caller=_caller(
        [{"text": f"Bullet {i} about mining.", "kind": KIND_FACT} for i in range(9)]
    ))
    assert len(out.bullets) <= 5


# ---------- no fallback, ever ----------


def test_a_failed_generation_produces_nothing_not_computed_prose(db):
    def _boom(_s, _u, _sc=None):
        raise RuntimeError("503 model overloaded")

    out = generate_pulse(PAYLOAD, target=db, gemini_caller=_boom)
    assert out.status == STATUS_FAILED
    assert out.bullets == []
    assert "503" in (out.note or "")


def test_a_malformed_response_produces_nothing(db):
    out = generate_pulse(PAYLOAD, target=db, gemini_caller=lambda *a, **k: {"unexpected": "shape"})
    assert out.status == STATUS_FAILED
    assert out.bullets == []


def test_an_empty_window_is_not_worth_a_call(db):
    calls = []

    def _call(_s, _u, _sc=None):
        calls.append(1)
        return {"bullets": []}

    empty = {**PAYLOAD, "collection": {**PAYLOAD["collection"], "collected": 0}}
    out = generate_pulse(empty, target=db, gemini_caller=_call)
    assert calls == []
    assert out.status == STATUS_FAILED


def test_the_slack_digest_omits_the_section_rather_than_substituting(db):
    """The regression this whole design exists to prevent: template arithmetic
    appearing where a written summary belongs."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    text = build_digest(db, since=since, pulse=None)

    assert "Market Pulse" not in text
    assert "Geographic split" not in text, "the old computed bullets came back"


def test_the_slack_digest_marks_an_interpretation(db):
    since = datetime.now(timezone.utc) - timedelta(days=7)
    text = build_digest(db, since=since, pulse=[
        {"text": "Maintenance clustering suggests a shutdown.", "kind": KIND_INTERPRETATION},
        {"text": "146 signals collected.", "kind": KIND_FACT},
    ])
    assert "_(interpretation)_" in text
    assert "146 signals collected." in text
    assert text.count("_(interpretation)_") == 1, "a fact was marked as a reading"


# ---------- storage ----------


def test_a_generated_pulse_round_trips(db):
    out = generate_pulse(PAYLOAD, target=db, gemini_caller=_caller([
        {"text": "146 signals collected.", "kind": KIND_FACT},
    ]))
    save_pulse(out, window_from="A", window_to="B", target=db)

    loaded = load_pulse("A", "B", db)
    assert loaded["bullets"][0]["text"] == "146 signals collected."
    assert loaded["signalsAnalysed"] == 146


def test_a_failed_week_is_recorded_but_reads_as_absent(db):
    """The row exists so "why is there no Market Pulse?" has an answer. The
    reader still gets nothing, which is the point."""
    out = generate_pulse(PAYLOAD, target=db, gemini_caller=lambda *a, **k: {"bullets": []})
    save_pulse(out, window_from="A", window_to="B", target=db)

    assert load_pulse("A", "B", db) is None, "a failed week must not render"

    with connect(db, readonly=True) as conn:
        row = conn.execute(
            "SELECT status, note FROM digest_pulse WHERE window_from = 'A'"
        ).fetchone()
    assert row["status"] == STATUS_FAILED
    assert row["note"], "the reason was not recorded"


def test_rerunning_a_window_replaces_it(db):
    for text in ("first draft", "second draft"):
        out = generate_pulse(PAYLOAD, target=db,
                             gemini_caller=_caller([{"text": text, "kind": KIND_FACT}]))
        save_pulse(out, window_from="A", window_to="B", target=db)

    with connect(db, readonly=True) as conn:
        n = conn.execute("SELECT count(*) FROM digest_pulse").fetchone()[0]
    assert n == 1, "windows accumulated instead of replacing"
    assert load_pulse("A", "B", db)["bullets"][0]["text"] == "second draft"


def test_a_missing_window_is_simply_absent(db):
    assert load_pulse("nope", "nothing", db) is None
