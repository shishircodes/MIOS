"""Recording what happened to a match, and refusing to overclaim from it.

Two things are being protected here. The first is that the score stored with a
decision is the one the consultant was looking at, not one recomputed later —
otherwise the model is judged against itself, over signals collected after the
fact, and every row confirms whatever the current weights believe.

The second is that a handful of outcomes does not become a success rate. That
is the failure this codebase has already had to fix on the dashboard, the
velocity table and Mode Push momentum: showing a number because one can be
computed, rather than because it means anything.
"""
from __future__ import annotations

import pytest

from loader.db import connect
from loader.ingest import init_db
from push import outcomes
from push.outcomes import MIN_FOR_RATES, UnknownOutcome, for_profile, record, summary


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "outcomes.db"
    init_db(path)
    monkeypatch.setattr("push.outcomes.connect", lambda t=None, **kw: connect(path, **kw))
    return path


# ---------- the score is frozen at the decision ----------


def test_the_score_is_stored_as_it_stood(db):
    """Recomputing later would judge the decision against a model that did not
    exist when it was made."""
    record("p1", "BHP", "contacted", score=81, confidence="high", assessable=86,
           rank_shown=1, recorded_by="bd@easyskill.com")

    with connect(db, readonly=True) as conn:
        row = conn.execute("SELECT * FROM match_outcomes").fetchone()

    assert row["score"] == 81
    assert row["confidence"] == "high"
    assert row["assessable"] == 86
    assert row["rank_shown"] == 1
    assert row["recorded_by"] == "bd@easyskill.com"


def test_an_unknown_verb_is_refused_by_name(db):
    with pytest.raises(UnknownOutcome) as exc:
        record("p1", "BHP", "maybe_later")

    assert "maybe_later" in str(exc.value)


def test_a_decision_needs_both_a_profile_and_a_company(db):
    with pytest.raises(ValueError):
        record("", "BHP", "contacted")
    with pytest.raises(ValueError):
        record("p1", "", "contacted")


# ---------- the UI can see what was already decided ----------


def test_the_latest_decision_per_company_is_what_shows(db):
    """A company marked contacted and later placed is two facts about one
    approach. The screen should show where it ended up."""
    record("p1", "BHP", "contacted")
    record("p1", "BHP", "placed")

    assert for_profile("p1")["BHP"]["outcome"] == "placed"


def test_decisions_are_scoped_to_the_profile(db):
    record("p1", "BHP", "contacted")
    record("p2", "Rio Tinto", "not_relevant")

    assert set(for_profile("p1")) == {"BHP"}
    assert set(for_profile("p2")) == {"Rio Tinto"}


def test_a_missing_table_leaves_the_matches_usable(tmp_path, monkeypatch):
    """An unreadable outcomes table must not stop the ranking rendering."""
    path = tmp_path / "bare.db"
    with connect(path) as conn:
        conn.execute("CREATE TABLE placeholder (x TEXT)")
    monkeypatch.setattr("push.outcomes.connect", lambda t=None, **kw: connect(path, **kw))

    assert for_profile("p1") == {}


def test_a_decision_can_be_recorded_before_the_schema_has_been_applied(tmp_path, monkeypatch):
    """The schema is applied by init_db during a pipeline run, so the first
    consultant to press a button on a fresh deployment would otherwise get an
    error about a missing table."""
    path = tmp_path / "bare.db"
    with connect(path) as conn:
        conn.execute("CREATE TABLE placeholder (x TEXT)")
    monkeypatch.setattr("push.outcomes.connect", lambda t=None, **kw: connect(path, **kw))

    record("p1", "BHP", "contacted", score=70)

    assert for_profile("p1")["BHP"]["outcome"] == "contacted"


# ---------- it refuses to overclaim ----------


def test_a_thin_sample_reports_counts_and_not_a_rate(db):
    for i in range(4):
        record(f"p{i}", "BHP", "contacted", score=80)

    s = summary()
    assert s["total"] == 4
    assert s["readyToCalibrate"] is False
    assert str(MIN_FOR_RATES) in s["note"]


def test_enough_decisions_unlocks_calibration(db):
    for i in range(MIN_FOR_RATES):
        record(f"p{i}", "BHP", "contacted", score=80)

    assert summary()["readyToCalibrate"] is True


def test_the_same_approach_is_counted_once(db):
    """Marking a company contacted and then placed is one decision about one
    approach. Counting both would inflate the sample with the same event."""
    for i in range(3):
        record("p1", f"Co{i}", "contacted", score=70)
        record("p1", f"Co{i}", "placed", score=70)

    s = summary()
    assert s["total"] == 3
    assert s["counts"]["placed"] == 3
    assert s["counts"]["contacted"] == 0, "superseded by the stronger outcome"


def test_decisions_are_grouped_by_the_score_they_were_taken_at(db):
    """The question calibration will eventually ask: did high scores actually
    lead anywhere that low ones did not?"""
    record("p1", "A", "placed", score=90)
    record("p2", "B", "not_relevant", score=20)

    bands = {b["band"]: b for b in summary()["byBand"]}
    assert bands["80-100"]["placed"] == 1
    assert bands["0-39"]["not_relevant"] == 1


def test_an_unreadable_table_summarises_to_empty_rather_than_failing(tmp_path, monkeypatch):
    path = tmp_path / "bare.db"
    with connect(path) as conn:
        conn.execute("CREATE TABLE placeholder (x TEXT)")
    monkeypatch.setattr("push.outcomes.connect", lambda t=None, **kw: connect(path, **kw))

    s = summary()
    assert s["total"] == 0
    assert s["readyToCalibrate"] is False
