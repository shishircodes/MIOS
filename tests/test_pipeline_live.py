"""Tests for pipeline.live. Scraper and Slack are mocked; Gemini is faked."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loader.ingest import init_db
from pipeline.live import run_live_cycle


@pytest.fixture
def watchlist_file(tmp_path: Path) -> Path:
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "Newmont", "tier": "A", "sector": "mining",
         "notes": "", "aliases": ["Newmont Lihir", "Newmont Mining"]},
        {"company_name": "Ok Tedi", "tier": "A", "sector": "mining",
         "notes": "", "aliases": ["OTML", "Ok Tedi Mining"]},
    ]))
    return p


@pytest.fixture
def db(tmp_path: Path, watchlist_file: Path) -> Path:
    p = tmp_path / "live.db"
    init_db(p, watchlist_path=watchlist_file)
    return p


def _fake_gemini(payload: dict):
    """signal_analyst batches multiple signals into a single Gemini call and
    expects a list response, one item per signal in the prompt."""
    def _call(_sys, user_prompt, schema=None, **_kwargs):
        n = max(1, user_prompt.upper().count("SIGNAL "))
        return [dict(payload) for _ in range(n)]
    return _call


def test_live_cycle_scrape_classify_post(db, monkeypatch):
    fake_records = [
        {"source_url": "https://x/job/1",
         "raw_content": "Process Operator at Newmont Lihir, PNG. FIFO ex-Cairns 4/4."},
        {"source_url": "https://x/job/2",
         "raw_content": "Heavy Diesel Fitter at Ok Tedi Mining Limited (OTML), Tabubil PNG."},
    ]
    monkeypatch.setattr("pipeline.live.scrape", lambda **_kw: fake_records)
    # Force settings to point at the test DB and a non-placeholder webhook
    monkeypatch.setattr("pipeline.live.settings",
                        type("S", (), {"db_path": db,
                                       "slack_webhook_url": "https://hooks.slack.com/services/T/X/Y"})())

    fake = _fake_gemini({
        "company_name": "Newmont",
        "sector": "mining",
        "signal_category": "hiring_velocity",
        "review_cycle": "weekly",
        "watchlist_match": "Newmont",
        "is_new_prospect": False,
        "reasoning": "PNG hiring",
    })

    with patch("delivery.slack.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, text="ok")
        summary = run_live_cycle(
            scrape_limit=10,
            db_path=db,
            do_scrape=True,
            do_slack=True,
            gemini_caller=fake,
        )

    assert summary["scraped"] == 2
    assert summary["ingested"] == 2
    assert summary["classified"] == 2
    assert summary["slack_ok"] is True
    assert summary["digest_chars"] > 0

    with sqlite3.connect(db) as c:
        n_classified = c.execute(
            "SELECT COUNT(*) FROM signals WHERE classified_at IS NOT NULL"
        ).fetchone()[0]
    assert n_classified == 2

    assert mock_post.call_count == 1
    posted_text = mock_post.call_args.kwargs["json"]["text"]
    assert "MIOS Weekly Intelligence" in posted_text


def test_live_cycle_scrape_failure_still_posts_digest(db, monkeypatch):
    # Scraper returns nothing — but a previously-classified row exists
    monkeypatch.setattr("pipeline.live.scrape", lambda **_kw: [])
    monkeypatch.setattr("pipeline.live.settings",
                        type("S", (), {"db_path": db,
                                       "slack_webhook_url": "https://hooks.slack.com/services/T/X/Y"})())

    classified_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(db) as c:
        c.execute(
            """INSERT INTO signals (
                signal_id, source_type, source_name, source_url, captured_at, geography,
                sector, company_name, watchlist_tier, signal_category, review_cycle,
                raw_content, analysis_notes, is_new_prospect, classified_at
            ) VALUES ('pre-1','job_board','synthetic','u/pre-1',?,'PNG',
                      'mining','Newmont','A','hiring_velocity','weekly',
                      'Maintenance Planner Newmont Lihir PNG, FIFO ex-Cairns rotation.',
                      'preexisting',0,?)""",
            (classified_at, classified_at),
        )
        c.commit()

    fake = _fake_gemini({
        "company_name": None, "sector": "other",
        "signal_category": "hiring_velocity", "review_cycle": "weekly",
        "watchlist_match": None, "is_new_prospect": False, "reasoning": "",
    })

    with patch("delivery.slack.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, text="ok")
        summary = run_live_cycle(
            scrape_limit=10, db_path=db, do_scrape=True, do_slack=True,
            gemini_caller=fake,
        )

    assert summary["scraped"] == 0
    assert summary["ingested"] == 0
    assert summary["classified"] == 0  # nothing pending to classify
    assert summary["slack_ok"] is True
    assert summary["digest_chars"] > 0
    assert mock_post.call_count == 1
    posted_text = mock_post.call_args.kwargs["json"]["text"]
    assert "Newmont" in posted_text  # the pre-existing classified row is in the digest
