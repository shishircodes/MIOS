"""The archived digest carries the week's Market Pulse.

It did not. `build_digest_payload` reads the pulse for the window, and the
pipeline builds that payload *before* generating the pulse — so the read
correctly found nothing, and the payload was archived with `marketPulse: None`
while the Slack message, built later from the same run, carried the bullets.

Every clean run stored the pulse as absent. It only ever looked correct when a
window was processed twice, because the second pass found the first pass's row
already in the table — which is exactly how it escaped notice: the runs anybody
examined by hand had all been run more than once.

The scheduled run of 6 September was the first straight-through one, and its
Market Pulse existed in `digest_pulse` with five bullets while the digest the
team opened showed none.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from delivery.pulse import STATUS_GENERATED, PulseOutcome
from loader.db import connect
from loader.ingest import init_db
from pipeline.live import run_live_cycle


@pytest.fixture
def watchlist_file(tmp_path: Path) -> Path:
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "Newmont", "tier": "A", "sector": "mining",
         "notes": "", "aliases": ["Newmont Lihir"]},
    ]))
    return p


@pytest.fixture
def db(tmp_path: Path, watchlist_file: Path) -> Path:
    p = tmp_path / "pulse.db"
    init_db(p, watchlist_path=watchlist_file)
    return p


def _classifier():
    def _call(_sys, user_prompt, schema=None, **_kw):
        n = max(1, user_prompt.upper().count("SIGNAL "))
        return [{
            "company_name": "Newmont",
            "sector": "mining",
            "signal_category": "hiring_velocity",
            "review_cycle": "weekly",
            "watchlist_match": "Newmont",
            "is_new_prospect": False,
            "reasoning": "PNG hiring",
        } for _ in range(n)]
    return _call


BULLETS = [
    {"text": "Australia generated 70 of the 80 signals collected this week.", "kind": "fact"},
    {"text": "Newmont's PNG hiring rose against its own baseline.", "kind": "interpretation"},
]


def _run(db, monkeypatch, *, pulse_ok=True):
    records = [
        {"source_url": "https://x/job/1",
         "raw_content": "Process Operator at Newmont Lihir, PNG. FIFO ex-Cairns 4/4."},
        {"source_url": "https://x/job/2",
         "raw_content": "Heavy Diesel Fitter at Newmont Lihir, PNG."},
    ]
    monkeypatch.setattr("pipeline.live.scrape_all", lambda **_kw: records)
    monkeypatch.setattr(
        "pipeline.live.settings",
        type("S", (), {"db_path": db, "slack_webhook_url": ""})())

    outcome = (PulseOutcome(BULLETS, STATUS_GENERATED, None, 2) if pulse_ok
               else PulseOutcome([], "failed", "quota exhausted", 2))
    monkeypatch.setattr("pipeline.live.generate_pulse", lambda *_a, **_kw: outcome)

    with patch("delivery.slack.requests.post") as post:
        post.return_value = MagicMock(status_code=200, text="ok")
        return run_live_cycle(scrape_limit=10, db_path=db, do_scrape=True,
                              do_slack=False, gemini_caller=_classifier())


def _archived(db):
    with connect(db, readonly=True) as conn:
        row = conn.execute(
            "SELECT payload FROM digests ORDER BY generated_at DESC LIMIT 1").fetchone()
    return json.loads(row["payload"]) if row else None


def test_a_single_clean_run_archives_its_pulse(db, monkeypatch):
    """The bug, stated as the property it broke. One pass, and the stored digest
    has the bullets."""
    _run(db, monkeypatch)

    stored = _archived(db)
    assert stored is not None, "the run should have archived a digest"
    assert stored["marketPulse"] is not None
    assert [b["text"] for b in stored["marketPulse"]["bullets"]] == \
        [b["text"] for b in BULLETS]


def test_the_archived_pulse_has_the_shape_the_api_serves(db, monkeypatch):
    """Read back through `load_pulse` rather than assigned from the outcome, so
    the archive and the live endpoint cannot describe the same digest
    differently."""
    _run(db, monkeypatch)

    pulse = _archived(db)["marketPulse"]
    assert set(pulse) == {"bullets", "signalsAnalysed", "generatedAt", "note"}
    assert pulse["signalsAnalysed"] == 2


def test_a_failed_pulse_archives_as_absent_not_as_empty_bullets(db, monkeypatch):
    """The other half: no pulse means the section is omitted, never replaced
    with computed prose or an empty list that renders as a blank heading."""
    _run(db, monkeypatch, pulse_ok=False)

    assert _archived(db)["marketPulse"] is None


def test_the_stored_digest_and_the_pulse_table_agree(db, monkeypatch):
    """They disagreed for every clean run: the table said generated, the archive
    said None."""
    _run(db, monkeypatch)

    with connect(db, readonly=True) as conn:
        row = conn.execute(
            "SELECT status, signals_analysed FROM digest_pulse "
            "ORDER BY generated_at DESC LIMIT 1").fetchone()

    assert row["status"] == STATUS_GENERATED
    stored = _archived(db)["marketPulse"]
    assert stored is not None
    assert stored["signalsAnalysed"] == row["signals_analysed"]
