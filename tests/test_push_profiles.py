"""Storing candidate profiles: editing, duplicates, validation, search, erasure.

Before this, saving an opened profile stored the person a second time, the same
email could be saved any number of times, "Australia" typed as a region was kept
as typed and then matched nothing, and the list stopped at fifty with nothing to
say there were more.
"""
from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from config.settings import settings as real_settings
from loader.db import connect
from loader.ingest import init_db
from push.store import (
    ProfileConflict,
    ProfileError,
    count_profiles,
    create_profile,
    delete_profile,
    get_profile,
    list_profiles,
    update_profile,
)

MARK = {"fullName": "Mark Anderson", "email": "Mark.Anderson@Example.com",
        "phone": "+61 412 555 019", "currentTitle": "Maintenance Planner",
        "sector": "mining", "region": "AU", "yearsExperience": 12, "skills": ["sap"]}


@pytest.fixture
def db(tmp_path, monkeypatch):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    path = tmp_path / "profiles.db"
    init_db(path, watchlist_path=wl)
    monkeypatch.setattr("loader.db.settings",
                        dataclasses.replace(real_settings, db_path=path, database_url=None))
    return path


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr("api.auth.settings", dataclasses.replace(real_settings, auth_disabled=True))
    from api.server import app

    return TestClient(app)


# ---------- editing ----------


def test_an_edit_updates_the_same_record(db):
    saved = create_profile(MARK, target=db)
    edited = update_profile(saved["id"], {**MARK, "currentTitle": "Senior Planner"}, target=db)
    assert edited["id"] == saved["id"]
    assert edited["currentTitle"] == "Senior Planner"
    assert edited["updatedAt"] is not None
    assert count_profiles(target=db) == 1


def test_an_edit_keeps_how_the_record_arrived(db):
    saved = create_profile(MARK, intake_source="cv_upload", source_filename="mark.pdf", target=db)
    edited = update_profile(saved["id"], {**MARK, "intakeSource": "manual_form"}, target=db)
    assert edited["intakeSource"] == "cv_upload"
    assert edited["sourceFilename"] == "mark.pdf"


def test_a_cleared_field_is_cleared(db):
    saved = create_profile(MARK, target=db)
    edited = update_profile(saved["id"], {**MARK, "phone": None, "skills": []}, target=db)
    assert edited["phone"] is None and edited["skills"] == []


def test_editing_a_missing_profile_returns_none(db):
    assert update_profile("prof-nope", MARK, target=db) is None


# ---------- duplicates ----------


def test_the_same_email_is_the_same_person(db):
    create_profile(MARK, target=db)
    with pytest.raises(ProfileConflict, match="already saved"):
        create_profile({**MARK, "fullName": "M. Anderson", "email": "mark.anderson@example.com",
                        "phone": None}, target=db)


def test_the_same_phone_is_only_a_duplicate_with_the_same_name(db):
    create_profile({**MARK, "email": None}, target=db)
    with pytest.raises(ProfileConflict):
        create_profile({**MARK, "email": None, "phone": "0412 555 019".replace("0", "+61 ", 1)},
                       target=db)
    # A shared office number is not the same person.
    create_profile({**MARK, "email": None, "fullName": "Sam Lee"}, target=db)


def test_an_edit_cannot_take_another_profiles_email(db):
    create_profile(MARK, target=db)
    other = create_profile({"fullName": "Sam Lee", "email": "sam@example.com"}, target=db)
    with pytest.raises(ProfileConflict):
        update_profile(other["id"], {"fullName": "Sam Lee", "email": MARK["email"]}, target=db)


def test_an_edit_may_keep_its_own_email(db):
    saved = create_profile(MARK, target=db)
    assert update_profile(saved["id"], MARK, target=db)["email"] == "mark.anderson@example.com"


# ---------- validation ----------


def test_emails_are_checked_and_lower_cased(db):
    assert create_profile(MARK, target=db)["email"] == "mark.anderson@example.com"
    with pytest.raises(ProfileError, match="not an email"):
        create_profile({"fullName": "X", "email": "not-an-email"}, target=db)


def test_phone_numbers_are_checked(db):
    with pytest.raises(ProfileError, match="phone"):
        create_profile({"fullName": "X", "phone": "12"}, target=db)


@pytest.mark.parametrize("typed,stored", [("Australia", "AU"), ("png", "PNG"),
                                          ("Papua New Guinea", "PNG")])
def test_regions_are_stored_as_the_matcher_reads_them(db, typed, stored):
    assert create_profile({"fullName": typed, "region": typed}, target=db)["region"] == stored


def test_an_unknown_region_is_refused(db):
    with pytest.raises(ProfileError, match="Region"):
        create_profile({"fullName": "X", "region": "New Zealand"}, target=db)


@pytest.mark.parametrize("typed,stored", [("Oil & Gas", "oil_gas"), ("MINING", "mining"),
                                          ("energy transition", "energy_transition")])
def test_sectors_are_normalised(db, typed, stored):
    assert create_profile({"fullName": typed, "sector": typed}, target=db)["sector"] == stored


def test_an_unknown_sector_is_refused(db):
    with pytest.raises(ProfileError, match="Sector"):
        create_profile({"fullName": "X", "sector": "aviation"}, target=db)


# ---------- search ----------


def test_search_finds_by_name_title_email_or_skill(db):
    create_profile(MARK, target=db)
    create_profile({"fullName": "Sam Lee", "currentTitle": "Electrician", "skills": ["hv"]},
                   target=db)
    assert [p["fullName"] for p in list_profiles(q="electric", target=db)] == ["Sam Lee"]
    assert [p["fullName"] for p in list_profiles(q="EXAMPLE.COM", target=db)] == ["Mark Anderson"]
    assert [p["fullName"] for p in list_profiles(q="hv", target=db)] == ["Sam Lee"]
    assert count_profiles(q="planner", target=db) == 1


def test_the_most_recently_edited_is_listed_first(db):
    first = create_profile({"fullName": "First"}, target=db)
    create_profile({"fullName": "Second"}, target=db)
    update_profile(first["id"], {"fullName": "First"}, target=db)
    assert list_profiles(target=db)[0]["fullName"] == "First"


# ---------- erasure ----------


def test_deleting_a_profile_clears_notes_that_may_name_them(db):
    from push.outcomes import record

    saved = create_profile(MARK, target=db)
    record(saved["id"], "BHP", "contacted", note="Mark keen, call his wife after 5",
           target=db)
    assert delete_profile(saved["id"], target=db)
    with connect(db) as conn:
        row = conn.execute("SELECT company_name, outcome, note FROM match_outcomes").fetchone()
    assert row["note"] is None
    assert row["outcome"] == "contacted", "the decision is kept for calibration"
    assert get_profile(saved["id"], target=db) is None


# ---------- over HTTP ----------


def test_put_updates_and_post_refuses_a_duplicate(client):
    created = client.post("/api/push/profiles", json=MARK)
    assert created.status_code == 201
    pid = created.json()["id"]

    again = client.post("/api/push/profiles", json=MARK)
    assert again.status_code == 409
    assert "Mark Anderson is already saved" in again.json()["detail"]

    edited = client.put(f"/api/push/profiles/{pid}", json={**MARK, "yearsExperience": 13})
    assert edited.status_code == 200 and edited.json()["yearsExperience"] == 13
    assert client.put("/api/push/profiles/prof-nope", json=MARK).status_code == 404
    assert client.put(f"/api/push/profiles/{pid}", json={**MARK, "region": "NZ"}).status_code == 400


def test_the_list_reports_its_total(client):
    for i in range(3):
        client.post("/api/push/profiles", json={"fullName": f"Person {i}"})
    body = client.get("/api/push/profiles", params={"limit": 2}).json()
    assert len(body["profiles"]) == 2 and body["total"] == 3
    assert client.get("/api/push/profiles", params={"q": "person 1"}).json()["total"] == 1


def test_candidates_for_an_agency_say_why_there_are_none(client, db):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, captured_at, "
            "geography, sector, company_name, signal_category, review_cycle, raw_content, "
            "classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("a1", "job_board", "seek", "https://x/a1", now, "AU", "mining",
             "Allstar Recruitment Group", "hiring_velocity", "weekly",
             "Maintenance Planner | Allstar Recruitment Group | Perth", now))
    client.post("/api/push/profiles", json=MARK)
    body = client.get("/api/push/company-candidates",
                      params={"company": "Allstar Recruitment Group"}).json()
    assert body["candidates"] == []
    assert body["excludedBecause"] == "agency"
