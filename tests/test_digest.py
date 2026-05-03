"""Tests for delivery.digest and delivery.slack."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from delivery.digest import build_digest, infer_geography
from delivery.slack import post_digest
from loader.ingest import init_db


@pytest.fixture
def watchlist_file(tmp_path: Path) -> Path:
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining",
         "notes": "", "aliases": []},
        {"company_name": "Newmont", "tier": "A", "sector": "mining",
         "notes": "", "aliases": []},
        {"company_name": "Downer", "tier": "A", "sector": "construction",
         "notes": "", "aliases": []},
    ]))
    return p


@pytest.fixture
def db(tmp_path: Path, watchlist_file: Path) -> Path:
    p = tmp_path / "test.db"
    init_db(p, watchlist_path=watchlist_file)
    return p


def _insert_classified(db: Path, *, sid: str, company: str | None, tier: str | None,
                       sector: str, category: str, cycle: str = "weekly",
                       new_prospect: bool = False, raw: str = "x" * 80,
                       captured_at: str | None = None, notes: str = "auto") -> None:
    captured_at = captured_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    classified_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(db) as c:
        c.execute(
            """INSERT INTO signals (
                signal_id, source_type, source_name, source_url, captured_at, geography,
                sector, company_name, watchlist_tier, signal_category, review_cycle,
                raw_content, analysis_notes, is_new_prospect, classified_at
            ) VALUES (?, 'job_board', 'syn', ?, ?, 'PNG', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, f"u/{sid}", captured_at, sector, company, tier, category, cycle,
             raw, notes, int(new_prospect), classified_at),
        )
        c.commit()


# ---------- geography inference ----------

def test_infer_geography_png():
    assert infer_geography("Process Operator at Lihir gold mine, PNG") == "PNG"
    assert infer_geography("Maintenance role Tabubil Ok Tedi") == "PNG"


def test_infer_geography_au_default():
    assert infer_geography("Maintenance Planner BHP Newman WA") == "AU"


# ---------- digest sections ----------

def test_build_digest_includes_all_five_sections(db):
    # Both PNG so they fall in the same geography bucket; leadership should rank first.
    _insert_classified(db, sid="s1", company="Newmont", tier="A", sector="mining",
                       category="hiring_velocity",
                       raw="Process operators Newmont Lihir PNG, FIFO from Cairns.")
    _insert_classified(db, sid="s2", company="Newmont", tier="A", sector="mining",
                       category="leadership",
                       raw="GM Lihir Operations - Newmont, PNG. Site GM oversees ~3500 personnel.",
                       notes="GM succession at Lihir")
    _insert_classified(db, sid="s3", company="Kumul Petroleum", tier=None, sector="oil_gas",
                       category="hiring_velocity", new_prospect=True,
                       raw="Reservoir engineer Kumul Petroleum Port Moresby, PNG state operator.")

    out = build_digest(db, since=datetime.now(timezone.utc) - timedelta(days=7))

    assert "MIOS Weekly Intelligence" in out and "Week of" in out
    assert "Key Signals This Week" in out
    assert "Market Pulse" in out
    assert "Hiring Velocity" in out and "Top 10 Watchlist Clients" in out
    assert "New Names (Not in Watchlist)" in out
    # Within the PNG bucket, leadership ranks above hiring_velocity.
    assert out.index("leadership") < out.index("hiring velocity")


def test_build_digest_groups_by_geography(db):
    _insert_classified(db, sid="au1", company="BHP", tier="A", sector="mining",
                       category="hiring_velocity",
                       raw="Maintenance role BHP Newman Pilbara WA, FIFO ex-Perth.")
    _insert_classified(db, sid="png1", company="Newmont", tier="A", sector="mining",
                       category="hiring_velocity",
                       raw="Process operator Newmont Lihir, PNG. FIFO ex-Cairns.")
    out = build_digest(db, since=datetime.now(timezone.utc) - timedelta(days=7))
    assert "AUSTRALIA" in out
    assert "PAPUA NEW GUINEA" in out
    # AU shown first per builder ordering
    assert out.index("AUSTRALIA") < out.index("PAPUA NEW GUINEA")


def test_build_digest_excludes_unclassified(db):
    # raw insert without classified_at -> should not appear in digest
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, raw_content) VALUES "
            "('u-pending','job_board','syn','u/p',?,'PNG','some pending content')",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
        )
        c.commit()
    _insert_classified(db, sid="ok", company="BHP", tier="A", sector="mining",
                       category="hiring_velocity",
                       raw="BHP Pilbara maintenance hiring this week, multiple roles open.")
    out = build_digest(db, since=datetime.now(timezone.utc) - timedelta(days=7))
    assert "some pending content" not in out
    assert "BHP" in out


def test_build_digest_respects_since_window(db):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    _insert_classified(db, sid="old", company="BHP", tier="A", sector="mining",
                       category="hiring_velocity", captured_at=old_ts,
                       raw="Old BHP signal from a month ago.")
    out = build_digest(db, since=datetime.now(timezone.utc) - timedelta(days=7))
    assert "_No classified signals in the reporting window._" in out


def test_build_digest_new_prospect_table(db):
    _insert_classified(db, sid="np1", company="Pilbara Minerals", tier=None,
                       sector="mining", category="project", new_prospect=True,
                       raw="Pilbara Minerals expanding Pilgangoora WA spodumene flotation circuit.")
    out = build_digest(db, since=datetime.now(timezone.utc) - timedelta(days=7))
    assert "Pilbara Minerals" in out
    assert "New Names (Not in Watchlist)" in out


# ---------- slack post ----------

def test_post_digest_success():
    with patch("delivery.slack.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, text="ok")
        ok = post_digest("https://hooks.slack.com/services/X/Y/Z", "*hello*")
    assert ok is True
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["text"] == "*hello*"


def test_post_digest_non_200_returns_false():
    with patch("delivery.slack.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=500, text="server error")
        ok = post_digest("https://hooks.slack.com/services/X/Y/Z", "x")
    assert ok is False


def test_post_digest_empty_url_returns_false():
    assert post_digest("", "x") is False


def test_post_digest_swallows_request_exception():
    import requests as r
    with patch("delivery.slack.requests.post", side_effect=r.ConnectionError("boom")):
        assert post_digest("https://x", "x") is False
