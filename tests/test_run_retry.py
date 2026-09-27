"""The admin's retry for a run that left records unclassified.

Pinned: it classifies what the run left, rebuilds that run's digest and pulse,
and clears the run's note; when the provider is still failing it says so on the
run instead; and it refuses to overlap a pipeline run or another retry, in both
directions.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from config.settings import settings as real_settings
from loader import run_log
from loader.db import connect
from loader.digest_archive import load_digest
from loader.ingest import ingest, init_db
from pipeline import retry


class ServerError(Exception):
    pass


@pytest.fixture
def db(tmp_path, monkeypatch):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([{"company_name": "BHP", "tier": "A", "sector": "mining",
                               "notes": "", "aliases": []}]))
    path = tmp_path / "retry.db"
    init_db(path, watchlist_path=wl)
    monkeypatch.setattr("loader.db.settings",
                        dataclasses.replace(real_settings, db_path=path, database_url=None))
    return path


def classify_ok(_sys, prompt, schema=None, **_kw):
    n = prompt.upper().count("--- SIGNAL ")
    return [{"company_name": "BHP", "sector": "mining", "signal_category": "hiring_velocity",
             "review_cycle": "weekly", "watchlist_match": "BHP", "is_new_prospect": False,
             "reasoning": "x"} for _ in range(n)]


def always_down(*_a, **_k):
    raise ServerError("503 UNAVAILABLE")


def pulse_ok(*_a, **_k):
    return {"bullets": [{"text": f"BHP is hiring planners, point {i}.", "kind": "fact"}
                        for i in range(3)]}


def incomplete_run(db, *, waiting=6, done=4):
    """A finished run, as 27 Sep 2026 left it: some rows read, some not."""
    rid = run_log.claim(trigger=run_log.TRIGGER_SCHEDULE, target=db)
    ingest([{"source_url": f"u{i}", "raw_content":
             f"Maintenance Planner {i} at BHP Pilbara, FIFO 8/6 ex-Perth, roles open."}
            for i in range(waiting + done)], db, run_id=rid)
    with connect(db) as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT signal_id FROM signals WHERE run_id = ? ORDER BY signal_id", (rid,))]
        for sid in ids[:done]:
            conn.execute("UPDATE signals SET classified_at = captured_at, sector = 'mining', "
                         "company_name = 'BHP', signal_category = 'hiring_velocity' "
                         "WHERE signal_id = ?", (sid,))
    run_log.finish(rid, status=run_log.STATUS_OK, collected=waiting + done,
                   note=f"{waiting} collected rows were left unclassified (the classifier "
                        f"failed on {waiting}); they are retried on the next run", target=db)
    return rid


def test_a_retry_finishes_the_run(db):
    rid = incomplete_run(db)
    out = retry.retry_unclassified(rid, by="admin@easyskill.com", target=db,
                                   gemini_caller=classify_ok, pulse_caller=pulse_ok)
    assert (out["waiting"], out["classified"], out["left"]) == (6, 6, 0)
    assert run_log.get(rid, db)["note"] is None, "no longer reads Incomplete"
    stored = load_digest(rid, target=db)
    assert stored is not None
    assert stored["collection"]["collected"] == 10, "the rebuilt digest includes them"
    assert stored["marketPulse"], "and so does the week's read"


def test_a_provider_still_down_is_said_on_the_run(db):
    rid = incomplete_run(db)
    out = retry.retry_unclassified(rid, by="admin@easyskill.com", target=db,
                                   gemini_caller=always_down, pulse_caller=pulse_ok)
    assert out["left"] == 6
    note = run_log.get(rid, db)["note"]
    assert "6 collected rows were left unclassified" in note
    assert "Retried by admin@easyskill.com — 0 of 6 classified" in note


def test_a_classifier_that_cannot_start_is_said_on_the_run(db, monkeypatch):
    """Found by pressing the button with no AI key: the task crashed, and the
    run looked exactly as it had before, with no reason anywhere on screen."""
    from llm.providers import ProviderNotConfigured

    def no_model(*_a, **_k):
        raise ProviderNotConfigured("No Gemini API key")

    monkeypatch.setattr("agents.signal_analyst.classify_pending", no_model)
    monkeypatch.setattr("pipeline.retry.classify_pending", no_model)
    rid = incomplete_run(db)
    out = retry.retry_unclassified(rid, by="admin@easyskill.com", target=db)
    assert out["failure"] == "No Gemini API key"
    note = run_log.get(rid, db)["note"]
    assert note.startswith("6 collected rows are still unclassified.")
    assert "could not start: No Gemini API key" in note


def test_nothing_waiting_just_clears_the_note(db):
    rid = incomplete_run(db, waiting=0)
    out = retry.retry_unclassified(rid, by="a", target=db, gemini_caller=always_down)
    assert out["waiting"] == 0
    assert run_log.get(rid, db)["note"] is None


def test_a_retry_does_not_overlap_a_pipeline_run(db):
    rid = incomplete_run(db)
    run_log.claim(trigger=run_log.TRIGGER_MANUAL, target=db)   # in flight
    with pytest.raises(retry.RetryRefused, match="pipeline run is in progress"):
        retry.retry_unclassified(rid, by="a", target=db, gemini_caller=classify_ok)


def test_an_unknown_run_is_refused(db):
    with pytest.raises(retry.RetryRefused, match="no such run"):
        retry.retry_unclassified("nope", by="a", target=db)


def test_two_retries_do_not_run_at_once(db, monkeypatch):
    rid = incomplete_run(db)
    monkeypatch.setattr(retry, "_state", {"runId": rid, "startedBy": "b", "startedAt": "t"})
    with pytest.raises(retry.RetryRefused, match="already running"):
        retry.retry_unclassified(rid, by="a", target=db, gemini_caller=classify_ok)


def test_the_scheduler_waits_for_a_retry(monkeypatch):
    from api import scheduler

    started = []
    monkeypatch.setattr(scheduler, "due_occurrence", lambda *_a: object())
    monkeypatch.setattr(scheduler.run_log, "has_run_for", lambda *_a: False)
    monkeypatch.setattr("pipeline.retry.in_progress", lambda: {"runId": "r"})

    async def fake_run(**kw):
        started.append(kw)

    monkeypatch.setattr(scheduler, "run_pipeline", fake_run)
    asyncio.run(scheduler._tick(sched=type("S", (), {"describe": lambda self: "Mon 05:00"})()))
    assert started == []


# ---------- over HTTP ----------


def _admin(monkeypatch) -> TestClient:
    monkeypatch.setattr("api.auth.settings",
                        dataclasses.replace(real_settings, auth_disabled=True))
    from api.server import app

    return TestClient(app)


def test_the_schedule_shows_what_each_incomplete_run_left(db, monkeypatch):
    rid = incomplete_run(db)
    body = _admin(monkeypatch).get("/api/admin/schedule").json()
    row = next(r for r in body["history"] if r["id"] == rid)
    assert row["waiting"] == 6
    assert body["retrying"] is None


def test_the_endpoint_refuses_during_a_run(db, monkeypatch):
    rid = incomplete_run(db)
    run_log.claim(trigger=run_log.TRIGGER_MANUAL, target=db)
    r = _admin(monkeypatch).post(f"/api/admin/schedule/runs/{rid}/retry")
    assert r.status_code == 409 and "in progress" in r.json()["detail"]


def test_run_now_waits_for_a_retry(db, monkeypatch):
    monkeypatch.setattr("api.admin_api.retry_in_progress", lambda: {"runId": "r"})
    r = _admin(monkeypatch).post("/api/admin/schedule/run")
    assert r.status_code == 409 and "retry" in r.json()["detail"]


def test_only_administrators_can_retry(db, monkeypatch):
    rid = incomplete_run(db)
    monkeypatch.setattr("api.auth.settings",
                        dataclasses.replace(real_settings, auth_disabled=False))
    monkeypatch.setattr("api.auth.current_user",
                        lambda request: {"email": "member@easyskill.com", "name": "Member"})
    from api.server import app

    assert TestClient(app).post(f"/api/admin/schedule/runs/{rid}/retry").status_code == 403
