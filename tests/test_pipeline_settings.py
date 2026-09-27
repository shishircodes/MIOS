"""Run limits an administrator sets: records per source, and AI batching.

Pinned: the table is pre-filled with the values the code used as constants,
pre-filling never overwrites a choice, a run actually uses the stored values,
bad input is refused whole, and reading never fails a run.
"""
from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from agents.signal_analyst import classify_pending
from config.settings import settings as real_settings
from loader import pipeline_settings as ps
from loader.db import connect
from loader.ingest import ingest, init_db
from scraper import SOURCE_NAMES


@pytest.fixture
def db(tmp_path, monkeypatch):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    path = tmp_path / "limits.db"
    init_db(path, watchlist_path=wl)
    monkeypatch.setattr("loader.db.settings",
                        dataclasses.replace(real_settings, db_path=path, database_url=None))
    return path


def _rows(db):
    with connect(db, readonly=True) as conn:
        return {r["key"]: dict(r) for r in conn.execute("SELECT * FROM pipeline_settings")}


# ---------- pre-filled ----------


def test_the_table_is_prefilled_with_the_values_the_code_used(db):
    rows = _rows(db)
    for name in SOURCE_NAMES:
        assert rows[f"scrape_limit:{name}"]["value"] == 50
    assert rows["classify.batch_size"]["value"] == 25
    assert rows["classify.max_chars"]["value"] == 3000
    assert rows["classify.daily_calls"]["value"] == 20
    assert rows["classify.min_seconds"]["value"] == 13
    assert {r["changed_by"] for r in rows.values()} == {"default"}


def test_prefilling_again_never_overwrites_a_choice(db):
    ps.update({"classify.batch_size": 10}, changed_by="admin@easyskill.com", target=db)
    init_db(db, watchlist_path=None)
    assert _rows(db)["classify.batch_size"]["value"] == 10


def test_without_the_table_every_value_is_its_default(tmp_path, monkeypatch):
    empty = tmp_path / "empty.db"
    empty.touch()
    cfg = ps.classifier(empty)
    assert (cfg.batch_size, cfg.max_chars, cfg.daily_calls, cfg.min_seconds) == (25, 3000, 20, 13)
    assert set(ps.scrape_limits(empty).values()) == {50}


# ---------- changing them ----------


def test_a_change_is_recorded_with_who_made_it(db):
    changed = ps.update({"scrape_limit:adzuna": 120}, changed_by="admin@easyskill.com", target=db)
    assert changed == ["scrape_limit:adzuna"]
    assert ps.scrape_limits(db)["adzuna"] == 120
    entry = next(s for s in ps.describe(db)["sources"] if s["source"] == "adzuna")
    assert entry["changedBy"] == "admin@easyskill.com"


def test_an_unchanged_value_is_not_rewritten(db):
    assert ps.update({"classify.batch_size": 25}, changed_by="x", target=db) == []
    assert _rows(db)["classify.batch_size"]["changed_by"] == "default"


@pytest.mark.parametrize("values,message", [
    ({"classify.batch_size": 100}, "between 5 and 50"),
    ({"classify.batch_size": 4}, "between 5 and 50"),
    ({"scrape_limit:adzuna": 0}, "between 1 and 500"),
    ({"classify.max_chars": 12.5}, "whole number"),
    ({"classify.daily_calls": "lots"}, "whole number"),
    ({"classify.min_seconds": True}, "whole number"),
    ({"scrape_limit:nowhere": 10}, "not a setting"),
])
def test_bad_values_are_refused(db, values, message):
    with pytest.raises(ps.SettingError, match=message):
        ps.update(values, changed_by="x", target=db)


def test_one_bad_value_saves_nothing(db):
    with pytest.raises(ps.SettingError):
        ps.update({"classify.batch_size": 10, "classify.daily_calls": 0}, changed_by="x", target=db)
    assert ps.classifier(db).batch_size == 25


def test_a_value_edited_by_hand_out_of_range_is_clamped(db):
    with connect(db) as conn:
        conn.execute("UPDATE pipeline_settings SET value = 400 WHERE key = 'classify.batch_size'")
    assert ps.classifier(db).batch_size == 50


def test_the_panel_shows_what_the_numbers_add_up_to(db):
    ps.update({"classify.batch_size": 40, "classify.daily_calls": 50}, changed_by="x", target=db)
    assert ps.describe(db)["derived"]["recordsPerDay"] == 2000


# ---------- a run uses them ----------


def _fake(sizes):
    def call(_sys, prompt, schema=None, **_kw):
        n = prompt.upper().count("--- SIGNAL ")
        sizes.append(n)
        return [{"company_name": "BHP", "sector": "mining", "signal_category": "hiring_velocity",
                 "review_cycle": "weekly", "watchlist_match": None, "is_new_prospect": False,
                 "reasoning": "x"} for _ in range(n)]
    return call


def _pending(db, n, text="Maintenance Planner {i} at BHP Pilbara, FIFO 8/6 ex-Perth, roles open."):
    ingest([{"source_url": f"u{i}", "raw_content": text.format(i=i)} for i in range(n)], db)


def test_classification_uses_the_stored_batch_size(db):
    ps.update({"classify.batch_size": 5}, changed_by="x", target=db)
    _pending(db, 12)
    sizes: list[int] = []
    counts = classify_pending(db, gemini_caller=_fake(sizes))
    assert sizes == [5, 5, 2]
    assert counts["classified"] == 12


def test_classification_stops_at_the_stored_daily_calls(db):
    ps.update({"classify.batch_size": 5, "classify.daily_calls": 1}, changed_by="x", target=db)
    _pending(db, 12)
    sizes: list[int] = []
    counts = classify_pending(db, gemini_caller=_fake(sizes))
    assert sizes == [5], "one call allowed, and the run asked for no more than it could make"
    assert counts["classified"] == 5


def test_records_are_cut_to_the_stored_length(db):
    ps.update({"classify.max_chars": 500}, changed_by="x", target=db)
    _pending(db, 1, text="Maintenance Planner at BHP Pilbara. " + "x" * 2000 + "END{i}")
    prompts: list[str] = []

    def call(_sys, prompt, schema=None, **_kw):
        prompts.append(prompt)
        return _fake([])(_sys, prompt, schema)

    classify_pending(db, gemini_caller=call)
    assert "END0" not in prompts[0]
    assert "x" * 400 in prompts[0]


def test_the_pipeline_scrapes_each_source_to_its_own_limit(db, monkeypatch):
    from pipeline import live

    ps.update({"scrape_limit:adzuna": 7}, changed_by="x", target=db)
    seen = {}

    def fake_scrape_all(**kw):
        seen.update(kw)
        return []

    monkeypatch.setattr("pipeline.live.scrape_all", fake_scrape_all)
    monkeypatch.setattr("pipeline.live.settings",
                        type("S", (), {"db_path": db, "slack_webhook_url": ""})())
    live.run_live_cycle(db_path=db, do_slack=False, do_pulse=False,
                        gemini_caller=_fake([]))
    assert seen["limits"]["adzuna"] == 7
    assert seen["limits"]["pngworkforce"] == 50

    seen.clear()
    live.run_live_cycle(db_path=db, scrape_limit=3, do_slack=False, do_pulse=False,
                        gemini_caller=_fake([]))
    assert seen["limits"] is None and seen["limit"] == 3, "--limit overrides every source"


# ---------- over HTTP ----------


def _client(monkeypatch, *, auth_disabled: bool) -> TestClient:
    monkeypatch.setattr("api.auth.settings",
                        dataclasses.replace(real_settings, auth_disabled=auth_disabled))
    from api.server import app

    return TestClient(app)


def test_only_administrators_can_read_or_change_them(db, monkeypatch):
    anon = _client(monkeypatch, auth_disabled=False)
    assert anon.get("/api/admin/pipeline-settings").status_code == 401
    monkeypatch.setattr("api.auth.current_user",
                        lambda request: {"email": "member@easyskill.com", "name": "Member"})
    assert anon.get("/api/admin/pipeline-settings").status_code == 403
    assert anon.put("/api/admin/pipeline-settings",
                    json={"values": {"classify.batch_size": 10}}).status_code == 403


def test_the_panel_round_trip(db, monkeypatch):
    admin = _client(monkeypatch, auth_disabled=True)
    body = admin.get("/api/admin/pipeline-settings").json()
    assert {s["source"] for s in body["sources"]} == set(SOURCE_NAMES)

    r = admin.put("/api/admin/pipeline-settings",
                  json={"values": {"scrape_limit:adzuna": 80, "classify.batch_size": 20}})
    assert r.status_code == 200
    assert sorted(r.json()["changed"]) == ["classify.batch_size", "scrape_limit:adzuna"]

    bad = admin.put("/api/admin/pipeline-settings", json={"values": {"classify.batch_size": 80}})
    assert bad.status_code == 400 and "between 5 and 50" in bad.json()["detail"]
    assert admin.put("/api/admin/pipeline-settings", json={}).status_code == 400

    sources = admin.get("/api/admin/sources").json()["sources"]
    assert next(s for s in sources if s["name"] == "adzuna")["limit"] == 80
