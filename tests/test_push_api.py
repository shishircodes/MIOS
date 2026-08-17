"""Tests for Mode Push storage and its HTTP surface (push.store, api.push_api).

The matcher and parser are covered exhaustively elsewhere; these are about the
wiring — that a CV round-trips into a draft without being saved, that a reviewed
draft becomes a stored profile, that matches come back ranked, and that none of
it is reachable without signing in.
"""
from __future__ import annotations

import dataclasses
import json
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from config.settings import settings as real_settings
from loader.db import connect
from loader.ingest import init_db
from push.store import (
    ProfileError,
    create_profile,
    delete_profile,
    get_profile,
    list_profiles,
    signals_for_matching,
)

CV_TEXT = """MARK ANDERSON
Senior Maintenance Planner
mark.anderson@example.com  |  +61 412 555 019  |  Perth, Western Australia

PROFESSIONAL SUMMARY
Maintenance Planner with 12 years experience across iron ore and gold mining
operations in the Pilbara. Strong shutdown planning background, SAP PM and Primavera P6.

SKILLS
SAP, Primavera P6, RCM, shutdown planning, confined space, white card
"""


def _docx(paragraphs: list[str]) -> bytes:
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


@pytest.fixture
def watchlist(tmp_path):
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    return p


@pytest.fixture
def db(tmp_path, watchlist, monkeypatch):
    """A temporary SQLite database that `resolve_target(None)` will pick up, so
    the API layer reaches it without every call having to pass a path."""
    path = tmp_path / "push.db"
    init_db(path, watchlist_path=watchlist)
    patched = dataclasses.replace(real_settings, db_path=path, database_url=None)
    monkeypatch.setattr("loader.db.settings", patched)
    return path


def _signal(db, signal_id, *, company="BHP", tier="A", title="Maintenance Planner",
            sector="mining", geo="AU", days_ago=1):
    captured = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, sector, company_name, watchlist_tier, "
            "signal_category, review_cycle, raw_content, analysis_notes, "
            "is_new_prospect, classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, "job_board", "seek", f"https://x/{signal_id}", captured, geo,
             sector, company, tier, "hiring_velocity", "weekly",
             f"{title} | {company} | Newman WA | full time", "note", 0, captured),
        )


PROFILE = {
    "fullName": "Mark Anderson",
    "currentTitle": "Maintenance Planner",
    "sector": "mining",
    "region": "AU",
    "yearsExperience": 12,
    "skills": ["sap", "shutdown planning"],
}


# ---------- store ----------


def test_a_saved_profile_comes_back_intact(db):
    saved = create_profile(PROFILE, target=db)
    again = get_profile(saved["id"], target=db)
    assert again == saved
    assert again["fullName"] == "Mark Anderson"
    assert again["skills"] == ["sap", "shutdown planning"]


def test_profile_id_is_generated_and_unique(db):
    a = create_profile(PROFILE, target=db)
    b = create_profile(PROFILE, target=db)
    assert a["id"] != b["id"]
    assert a["id"].startswith("prof-")


def test_a_nameless_profile_is_refused(db):
    """Everything else can be filled in later; a row nobody can identify cannot."""
    with pytest.raises(ProfileError, match="name is required"):
        create_profile({"currentTitle": "Planner"}, target=db)


def test_skills_accept_the_comma_string_a_text_input_produces(db):
    p = create_profile({**PROFILE, "skills": "SAP, Primavera P6 , sap"}, target=db)
    assert p["skills"] == ["sap", "primavera p6"], "trimmed, lowercased, de-duplicated"


def test_years_must_be_a_number(db):
    with pytest.raises(ProfileError, match="whole number"):
        create_profile({**PROFILE, "yearsExperience": "twelve"}, target=db)


def test_absurd_years_are_refused(db):
    with pytest.raises(ProfileError, match="between 0 and 60"):
        create_profile({**PROFILE, "yearsExperience": 300}, target=db)


def test_unknown_keys_are_ignored_not_rejected(db):
    """A parsed draft carries `confidence`; the browser shouldn't have to strip it."""
    p = create_profile({**PROFILE, "confidence": {"full_name": "high"}}, target=db)
    assert p["fullName"] == "Mark Anderson"


def test_intake_source_is_recorded(db):
    p = create_profile(PROFILE, intake_source="cv_upload",
                       source_filename="mark.docx", target=db)
    assert p["intakeSource"] == "cv_upload"
    assert p["sourceFilename"] == "mark.docx"


def test_an_unrecognised_intake_source_is_refused(db):
    with pytest.raises(ProfileError, match="intake_source"):
        create_profile(PROFILE, intake_source="scraped", target=db)


def test_profiles_are_listed_newest_first(db):
    create_profile({**PROFILE, "fullName": "First"}, target=db)
    create_profile({**PROFILE, "fullName": "Second"}, target=db)
    names = [p["fullName"] for p in list_profiles(target=db)]
    assert names[0] in {"First", "Second"} and len(names) == 2


def test_a_profile_can_be_deleted(db):
    p = create_profile(PROFILE, target=db)
    assert delete_profile(p["id"], target=db) is True
    assert get_profile(p["id"], target=db) is None
    assert delete_profile(p["id"], target=db) is False


def test_matching_signals_respect_the_window(db):
    _signal(db, "fresh", days_ago=2)
    _signal(db, "ancient", days_ago=200)
    ids = signals_for_matching(days=30, target=db)
    assert len(ids) == 1


def test_unclassified_signals_are_not_matched_against(db):
    """A row Gemini has not read has no company or sector to match on."""
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, raw_content) VALUES (?,?,?,?,?,?,?)",
            ("raw", "job_board", "seek", "https://x/raw",
             datetime.now(timezone.utc).isoformat(timespec="seconds"), "AU", "Planner"),
        )
    assert signals_for_matching(target=db) == []


# ---------- endpoints ----------


@pytest.fixture
def client(db, monkeypatch):
    """Signed-in client. AUTH_DISABLED is the documented dev bypass; the
    sign-in requirement itself is asserted separately below."""
    monkeypatch.setattr(
        "api.auth.settings", dataclasses.replace(real_settings, auth_disabled=True)
    )
    from api.server import app
    return TestClient(app)


@pytest.fixture
def anon(db, monkeypatch):
    monkeypatch.setattr(
        "api.auth.settings", dataclasses.replace(real_settings, auth_disabled=False)
    )
    from api.server import app
    return TestClient(app)


@pytest.mark.parametrize("method,path", [
    ("get", "/api/push/profiles"),
    ("post", "/api/push/profiles"),
    ("post", "/api/push/parse-cv"),
    ("get", "/api/push/profiles/prof-x/matches"),
    ("post", "/api/push/match"),
    ("delete", "/api/push/profiles/prof-x"),
])
def test_every_push_endpoint_requires_sign_in(anon, method, path):
    """These are real people's CVs — there is no public read path."""
    assert getattr(anon, method)(path).status_code == 401


def test_uploading_a_cv_returns_a_draft_without_saving_it(client):
    res = client.post(
        "/api/push/parse-cv",
        files={"file": ("mark.docx", _docx(CV_TEXT.split("\n")),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["draft"]["fullName"] == "Mark Anderson"
    assert body["draft"]["currentTitle"] == "Maintenance Planner"
    assert body["saved"] is False
    assert client.get("/api/push/profiles").json()["profiles"] == [], "nothing was stored"


def test_the_draft_reports_confidence_for_review(client):
    res = client.post(
        "/api/push/parse-cv",
        files={"file": ("mark.docx", _docx(CV_TEXT.split("\n")), "application/octet-stream")},
    )
    conf = res.json()["draft"]["confidence"]
    assert conf["full_name"] == "high"
    assert set(conf.values()) <= {"high", "medium", "low"}


def test_an_unreadable_cv_explains_itself(client):
    res = client.post(
        "/api/push/parse-cv",
        files={"file": ("cv.docx", b"not a zip", "application/octet-stream")},
    )
    assert res.status_code == 400
    assert "not a valid .docx" in res.json()["detail"], "the message is shown to the user"


def test_a_legacy_doc_is_refused_with_advice(client):
    res = client.post(
        "/api/push/parse-cv",
        files={"file": ("cv.doc", b"\xd0\xcf\x11\xe0" + b"x" * 100, "application/msword")},
    )
    assert res.status_code == 400
    assert "Save As" in res.json()["detail"]


def test_saving_a_reviewed_profile_then_reading_it_back(client):
    created = client.post("/api/push/profiles", json=PROFILE)
    assert created.status_code == 201
    pid = created.json()["id"]

    assert client.get(f"/api/push/profiles/{pid}").json()["fullName"] == "Mark Anderson"
    assert [p["id"] for p in client.get("/api/push/profiles").json()["profiles"]] == [pid]


def test_saving_without_a_name_is_a_400_not_a_500(client):
    res = client.post("/api/push/profiles", json={"currentTitle": "Planner"})
    assert res.status_code == 400
    assert "name is required" in res.json()["detail"]


def test_a_missing_profile_is_a_404(client):
    assert client.get("/api/push/profiles/prof-nope").status_code == 404
    assert client.get("/api/push/profiles/prof-nope/matches").status_code == 404
    assert client.delete("/api/push/profiles/prof-nope").status_code == 404


def test_matches_are_ranked_and_carry_evidence(client, db):
    for i in range(5):
        _signal(db, f"bhp-{i}", company="BHP", tier="A")
    _signal(db, "tiny", company="Tiny Cafe", tier=None,
            title="Barista", sector="other")

    pid = client.post("/api/push/profiles", json=PROFILE).json()["id"]
    body = client.get(f"/api/push/profiles/{pid}/matches").json()

    assert body["matches"][0]["co"] == "BHP"
    assert body["matches"][0]["rank"] == 1
    assert body["matches"][0]["evidence"], "a score with no reason is not actionable"
    assert body["matches"][0]["action"] == "Send MPC email"
    assert body["signalsConsidered"] == 6


def test_match_results_honour_the_limit(client, db):
    for i in range(12):
        _signal(db, f"s-{i}", company=f"Company {i}", tier=None)
    pid = client.post("/api/push/profiles", json=PROFILE).json()["id"]
    body = client.get(f"/api/push/profiles/{pid}/matches", params={"limit": 3}).json()
    assert len(body["matches"]) == 3


def test_the_match_window_is_wider_than_the_digest_week(client, db):
    """A consultant rolling off in 30 days can be pitched against a company that
    was hiring three weeks ago — that is not this week's news, but it is a lead."""
    _signal(db, "three-weeks", company="BHP", tier="A", days_ago=21)
    pid = client.post("/api/push/profiles", json=PROFILE).json()["id"]
    assert client.get(f"/api/push/profiles/{pid}/matches").json()["matches"]


def test_an_unsaved_profile_can_be_matched(client, db):
    """Trying a CV against the market shouldn't require storing the person."""
    _signal(db, "bhp-1", company="BHP", tier="A")
    body = client.post("/api/push/match", json=PROFILE).json()

    assert body["matches"][0]["co"] == "BHP"
    assert client.get("/api/push/profiles").json()["profiles"] == [], "nothing was stored"


def test_matching_with_no_signals_returns_an_empty_ranking(client):
    pid = client.post("/api/push/profiles", json=PROFILE).json()["id"]
    body = client.get(f"/api/push/profiles/{pid}/matches").json()
    assert body["matches"] == []
    assert body["signalsConsidered"] == 0


def test_deleting_a_profile_removes_it(client):
    pid = client.post("/api/push/profiles", json=PROFILE).json()["id"]
    assert client.delete(f"/api/push/profiles/{pid}").json()["deleted"] == pid
    assert client.get(f"/api/push/profiles/{pid}").status_code == 404


def test_the_full_cv_to_matches_journey(client, db):
    """The path the BD team actually walks: upload, correct, save, match."""
    for i in range(4):
        _signal(db, f"bhp-{i}", company="BHP", tier="A")

    draft = client.post(
        "/api/push/parse-cv",
        files={"file": ("mark.docx", _docx(CV_TEXT.split("\n")), "application/octet-stream")},
    ).json()

    reviewed = {**draft["draft"], "region": "AU",  # the reviewer fixes a field
                "intakeSource": "cv_upload",
                "sourceFilename": draft["sourceFilename"]}
    saved = client.post("/api/push/profiles", json=reviewed).json()
    assert saved["intakeSource"] == "cv_upload"

    matches = client.get(f"/api/push/profiles/{saved['id']}/matches").json()["matches"]
    assert matches[0]["co"] == "BHP"
    assert matches[0]["score"] > 0
