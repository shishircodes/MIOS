"""Tests for one digest per pipeline run, kept after later runs.

Two defects motivate this, and the tests are mostly about them:

* **Runs were blended.** The digest covered a rolling seven-day window, so a
  page load the day after a Monday scrape showed the previous Monday's signals
  beside that one, under a heading claiming a single week.
* **There was no past.** A new run changed what the one page said; the digest
  that had been sent to Slack could not be read back.

The third concern is subtler and is what most of these pin: an archived digest
must be *what was published*. Not a recomputation, and never a fallback — an
empty run has to report emptiness rather than borrowing another run's signals
or the synthetic demo set.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from api.digest_service import build_digest_payload
from loader.db import connect
from loader.digest_archive import (
    latest_digest,
    list_digests,
    load_digest,
    save_digest,
)
from loader.ingest import init_db


@pytest.fixture
def db(tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    path = tmp_path / "archive.db"
    init_db(path, watchlist_path=wl)
    return path


def add_signal(db, *, run_id: str | None, sid: str, captured: str, company: str = "BHP"):
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, region, sector, company_name, watchlist_tier, "
            "signal_category, review_cycle, raw_content, analysis_notes, is_new_prospect, "
            "classified_at, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, "job_board", "seek", f"https://x/{sid}", captured, "AU", "AU", "mining",
             company, "A", "hiring_velocity", "weekly", f"{company} is hiring an engineer",
             "note", 0, captured, run_id),
        )


def iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


# ---------- a digest covers one run, not a window ----------


def test_a_run_scoped_payload_holds_only_that_runs_signals(db):
    """The original complaint: one digest showed 25 August and 31 August
    together, because a rolling window spans however many runs happen to fall
    inside it."""
    for i in range(3):
        add_signal(db, run_id="run-a", sid=f"a{i}", captured=iso(7))
    for i in range(2):
        add_signal(db, run_id="run-b", sid=f"b{i}", captured=iso(0))

    assert len(build_digest_payload(db, run_id="run-a")["signals"]) == 3
    assert len(build_digest_payload(db, run_id="run-b")["signals"]) == 2


def test_without_a_run_the_window_still_spans_runs(db):
    """The old behaviour is intact for the live fallback, which is what the
    dashboard uses before anything has been archived."""
    add_signal(db, run_id="run-a", sid="a0", captured=iso(2))
    add_signal(db, run_id="run-b", sid="b0", captured=iso(0))

    assert len(build_digest_payload(db)["signals"]) == 2


def test_signals_from_no_run_are_not_attributed_to_one(db):
    """Rows collected before digests were kept per run have no run to belong
    to. Sweeping them into the nearest one would invent archive history."""
    add_signal(db, run_id=None, sid="orphan", captured=iso(1))
    add_signal(db, run_id="run-a", sid="a0", captured=iso(0))

    ids = [s["id"] for s in build_digest_payload(db, run_id="run-a")["signals"]]
    assert ids == ["a0"]


# ---------- an empty run must not borrow ----------


def test_an_empty_run_reports_nothing_rather_than_older_signals(db):
    """Falling back to "the latest we do have" would file another run's rows
    under this one — exactly the blending this feature removes."""
    add_signal(db, run_id="run-a", sid="a0", captured=iso(7))

    payload = build_digest_payload(db, run_id="run-empty")
    assert payload["signals"] == []
    assert payload["windowEmpty"] is False


def test_an_empty_run_never_falls_back_to_the_demo_data(db):
    """The dangerous one. The synthetic dataset exists so a fresh checkout has
    something to render; stored as a published digest it would put invented
    companies in front of somebody deciding who to contact."""
    payload = build_digest_payload(db, run_id="run-empty")

    assert payload["sourceMode"] == "live"
    assert payload["signals"] == []


def test_the_live_dashboard_still_falls_back(db):
    """That guard is scoped to run-based digests only. With no run asked for and
    an empty database, the synthetic fallback is still what keeps a fresh
    checkout usable."""
    payload = build_digest_payload(db)
    assert payload["sourceMode"] == "synthetic"
    assert payload["signals"]


# ---------- the archive ----------


def test_a_digest_round_trips(db):
    payload = {"weekLabel": "week of 24 August 2026", "signals": [{"id": "a0"}]}
    save_digest(run_id="run-a", payload=payload, window_from=iso(7), window_to=iso(6), target=db)

    loaded = load_digest("run-a", db)
    assert loaded["weekLabel"] == "week of 24 August 2026"
    assert loaded["archived"]["runId"] == "run-a"
    assert loaded["archived"]["signalCount"] == 1


def test_a_later_run_does_not_take_away_an_earlier_digest(db):
    """The second half of the request: past digests stay readable once a newer
    run exists. Before this, a new run simply changed what the page said."""
    save_digest(run_id="run-a", payload={"signals": [{"id": "a"}]},
                window_from=iso(14), window_to=iso(13), target=db)
    save_digest(run_id="run-b", payload={"signals": [{"id": "b"}]},
                window_from=iso(7), window_to=iso(6), target=db)

    assert load_digest("run-a", db) is not None
    assert latest_digest(db)["archived"]["runId"] == "run-b"
    assert [d["runId"] for d in list_digests(target=db)] == ["run-b", "run-a"]


def test_one_run_can_only_have_one_digest(db):
    """Re-running a cycle for the same run corrects its digest rather than
    leaving two. The run id is the primary key so the database guarantees it."""
    save_digest(run_id="run-a", payload={"signals": [{"id": "first"}]},
                window_from=iso(7), window_to=iso(6), target=db)
    save_digest(run_id="run-a", payload={"signals": [{"id": "second"}]},
                window_from=iso(7), window_to=iso(6), target=db)

    with connect(db, readonly=True) as conn:
        assert conn.execute("SELECT count(*) FROM digests").fetchone()[0] == 1
    assert load_digest("run-a", db)["signals"][0]["id"] == "second"


def test_the_slack_message_is_kept_with_the_digest(db):
    """So the archive can show what was actually sent, rather than re-rendering
    it from a payload and hoping the two still agree."""
    save_digest(run_id="run-a", payload={"signals": []}, window_from=iso(7),
                window_to=iso(6), digest_text="*MIOS Weekly*\nBHP is hiring.", target=db)

    assert "BHP is hiring." in load_digest("run-a", db)["digestText"]


def test_an_unreadable_payload_is_reported_as_absent(db):
    """Better no digest than a broken one: the endpoint falls back to a live
    computation, which is at least coherent."""
    save_digest(run_id="run-a", payload={"signals": []}, window_from=iso(7),
                window_to=iso(6), target=db)
    with connect(db) as conn:
        conn.execute("UPDATE digests SET payload = ? WHERE run_id = ?", ("{not json", "run-a"))

    assert load_digest("run-a", db) is None


def test_a_missing_digest_is_simply_absent(db):
    assert load_digest("nonexistent", db) is None
    assert latest_digest(db) is None
    assert list_digests(target=db) == []


def test_the_listing_carries_no_payloads(db):
    """It is a picker. Sending every payload to render a dropdown would grow
    with the archive and be discarded on arrival."""
    save_digest(run_id="run-a", payload={"signals": [{"id": "a"}], "velocity": [1, 2, 3]},
                window_from=iso(7), window_to=iso(6), target=db)

    entry = list_digests(target=db)[0]
    assert set(entry) == {"runId", "windowFrom", "windowTo", "generatedAt", "signalCount"}


# ---------- a run that produced nothing ----------


def test_a_run_that_produced_nothing_is_not_archived(db, monkeypatch, tmp_path):
    """Two failures land here — the scrape returned nothing, or nothing could be
    classified because the Gemini quota was spent. In both the team should still
    get the week's figures rather than a blank message, and the archive must not
    gain an entry claiming this run published a week it had no part in."""
    import pipeline.live as live

    add_signal(db, run_id="earlier", sid="old", captured=iso(2))

    monkeypatch.setattr(live, "scrape_all", lambda **kw: [])
    monkeypatch.setattr(live, "classify_pending", lambda *a, **k: {"classified": 0})
    monkeypatch.setattr(live, "generate_pulse",
                        lambda *a, **k: type("O", (), {"ok": False, "note": "n/a", "bullets": []})())
    monkeypatch.setattr(live, "save_pulse", lambda *a, **k: None)

    summary = live.run_live_cycle(db_path=db, do_slack=False)

    assert summary["archived"] is False, "an empty run should not be archived"
    assert list_digests(target=db) == []
    # The digest still carries the earlier signal, so a failed scrape does not
    # produce a blank weekly message.
    assert "BHP" in summary["digest"]


def test_the_archive_records_what_was_collected_not_what_was_shown(db):
    """`signals` is capped at MAX_SIGNALS_SHOWN for display. Counting it would
    label every week in the picker "40 signals" however much was gathered,
    making the one number that distinguishes the entries useless."""
    payload = {"signals": [{"id": f"s{i}"} for i in range(40)],
               "collection": {"collected": 137}}
    save_digest(run_id="run-a", payload=payload, window_from=iso(7),
                window_to=iso(6), target=db)

    assert list_digests(target=db)[0]["signalCount"] == 137


def test_a_payload_without_a_collection_block_falls_back_to_the_list(db):
    save_digest(run_id="run-a", payload={"signals": [{"id": "a"}, {"id": "b"}]},
                window_from=iso(7), window_to=iso(6), target=db)

    assert list_digests(target=db)[0]["signalCount"] == 2
