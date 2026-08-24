"""Tests for the report workflow and its HTTP surface.

Most of these exist to pin the review gate. §8.3 of the project spec requires
the human review step to be *architecturally enforced*, so "a reviewer is
expected to check it" is not enough — the code has to refuse.
"""
from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from config.settings import settings as real_settings
from loader.db import connect
from loader.ingest import init_db
from publish.store import (
    ReportError,
    approve_report,
    create_report,
    delete_report,
    get_report,
    list_reports,
    render_markdown,
    set_section_approval,
    update_section,
)

QUARTER = "2026-Q3"
REVIEWER = "founder@easyskill.com"


@pytest.fixture
def watchlist(tmp_path):
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    return p


@pytest.fixture
def db(tmp_path, watchlist, monkeypatch):
    path = tmp_path / "publish.db"
    init_db(path, watchlist_path=watchlist)
    patched = dataclasses.replace(real_settings, db_path=path, database_url=None)
    monkeypatch.setattr("loader.db.settings", patched)
    return path


def _signal(db, signal_id, *, company="BHP", when="2026-08-15T10:00:00+00:00"):
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, sector, company_name, watchlist_tier, "
            "signal_category, review_cycle, raw_content, analysis_notes, "
            "is_new_prospect, classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, "job_board", "seek", f"https://x/{signal_id}", when, "AU",
             "mining", company, None, "hiring_velocity", "quarterly",
             f"Maintenance Planner | {company} | Newman WA", "note", 0, when),
        )


def _approve_all(db, report):
    """Approve every section, writing the manual one first."""
    for s in report["sections"]:
        if s["empty"]:
            update_section(s["id"], "Outlook written by the reviewer.", target=db)
    for s in get_report(report["id"], target=db)["sections"]:
        set_section_approval(s["id"], True, REVIEWER, target=db)
    return get_report(report["id"], target=db)


# ---------- generating ----------


def test_a_new_report_starts_as_an_unapproved_draft(db):
    _signal(db, "s1")
    r = create_report(QUARTER, target=db, use_llm=False)
    assert r["status"] == "draft"
    assert r["sectionsApproved"] == 0
    assert r["canApprove"] is False
    assert r["signalsAnalysed"] == 1


def test_regenerating_does_not_overwrite_an_existing_draft(db):
    """Someone may be part way through editing. A second generate is a second
    draft, not a silent replacement of their work."""
    _signal(db, "s1")
    first = create_report(QUARTER, target=db, use_llm=False)
    update_section(first["sections"][0]["id"], "My careful edit.", target=db)

    second = create_report(QUARTER, target=db, use_llm=False)
    assert second["id"] != first["id"]
    assert get_report(first["id"], target=db)["sections"][0]["body"] == "My careful edit."
    assert len(list_reports(target=db)) == 2


def test_a_bad_quarter_is_refused(db):
    with pytest.raises(ReportError):
        create_report("2026-Q9", target=db, use_llm=False)


# ---------- the review gate ----------


def test_a_report_cannot_be_approved_while_sections_are_outstanding(db):
    _signal(db, "s1")
    r = create_report(QUARTER, target=db, use_llm=False)
    with pytest.raises(ReportError, match="Every section must be approved"):
        approve_report(r["id"], REVIEWER, target=db)


def test_the_outstanding_sections_are_named(db):
    _signal(db, "s1")
    r = create_report(QUARTER, target=db, use_llm=False)
    assert "Executive Summary" in r["outstanding"]
    assert len(r["outstanding"]) == r["sectionsTotal"]


def test_an_empty_section_cannot_be_approved(db):
    """The outlook ships blank on purpose. Approving it blank would let the gap
    reach a client under a tick."""
    _signal(db, "s1")
    r = create_report(QUARTER, target=db, use_llm=False)
    outlook = next(s for s in r["sections"] if s["source"] == "manual")
    with pytest.raises(ReportError, match="empty section cannot be approved"):
        set_section_approval(outlook["id"], True, REVIEWER, target=db)


def test_writing_the_outlook_makes_it_approvable(db):
    _signal(db, "s1")
    r = create_report(QUARTER, target=db, use_llm=False)
    outlook = next(s for s in r["sections"] if s["source"] == "manual")
    after = update_section(outlook["id"], "Demand is expected to hold.", target=db)
    written = next(s for s in after["sections"] if s["id"] == outlook["id"])
    assert written["empty"] is False
    set_section_approval(outlook["id"], True, REVIEWER, target=db)


def test_editing_a_section_clears_its_approval(db):
    """Otherwise a reviewer could approve text, change it, and keep the tick."""
    _signal(db, "s1")
    r = create_report(QUARTER, target=db, use_llm=False)
    sec = r["sections"][0]
    set_section_approval(sec["id"], True, REVIEWER, target=db)
    assert get_report(r["id"], target=db)["sections"][0]["approved"] is True

    after = update_section(sec["id"], "Rewritten after approval.", target=db)
    assert after["sections"][0]["approved"] is False
    assert after["sections"][0]["approvedBy"] is None


def test_approving_every_section_unlocks_the_report(db):
    _signal(db, "s1")
    r = _approve_all(db, create_report(QUARTER, target=db, use_llm=False))
    assert r["canApprove"] is True
    assert r["outstanding"] == []

    final = approve_report(r["id"], REVIEWER, target=db)
    assert final["status"] == "approved"
    assert final["approvedBy"] == REVIEWER


def test_an_approved_report_can_no_longer_be_edited(db):
    _signal(db, "s1")
    r = approve_report(_approve_all(db, create_report(QUARTER, target=db, use_llm=False))["id"],
                       REVIEWER, target=db)
    with pytest.raises(ReportError, match="can no longer be changed"):
        update_section(r["sections"][0]["id"], "sneaky change", target=db)


def test_a_report_cannot_be_approved_twice(db):
    _signal(db, "s1")
    r = _approve_all(db, create_report(QUARTER, target=db, use_llm=False))
    approve_report(r["id"], REVIEWER, target=db)
    with pytest.raises(ReportError, match="already approved"):
        approve_report(r["id"], REVIEWER, target=db)


def test_approval_can_be_withdrawn_before_sign_off(db):
    _signal(db, "s1")
    r = create_report(QUARTER, target=db, use_llm=False)
    sec = r["sections"][0]
    set_section_approval(sec["id"], True, REVIEWER, target=db)
    after = set_section_approval(sec["id"], False, REVIEWER, target=db)
    assert after["sections"][0]["approved"] is False


def test_an_edit_is_flagged_as_a_human_change(db):
    _signal(db, "s1")
    r = create_report(QUARTER, target=db, use_llm=False)
    assert r["sections"][0]["edited"] is False
    after = update_section(r["sections"][0]["id"], "Reworded.", target=db)
    assert after["sections"][0]["edited"] is True


# ---------- export ----------


def test_a_draft_export_is_marked_as_a_draft(db):
    _signal(db, "s1")
    md = render_markdown(create_report(QUARTER, target=db, use_llm=False))
    assert "DRAFT — NOT APPROVED FOR DISTRIBUTION" in md


def test_an_approved_export_names_its_approver_instead(db):
    _signal(db, "s1")
    r = _approve_all(db, create_report(QUARTER, target=db, use_llm=False))
    md = render_markdown(approve_report(r["id"], REVIEWER, target=db))
    assert "DRAFT" not in md
    assert f"Approved by {REVIEWER}" in md


def test_export_includes_every_section(db):
    _signal(db, "s1")
    md = render_markdown(create_report(QUARTER, target=db, use_llm=False))
    for heading in ("Executive Summary", "Papua New Guinea", "Methodology"):
        assert f"## {heading}" in md
    assert "This section has not been written" in md, "the blank outlook is visible"


# ---------- endpoints ----------


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Endpoint tests generate reports through the API, which now calls Gemini.

    Stubbing the rewrite keeps the suite offline and fast — a real call carries a
    13-second throttle, and a test that depends on a quota is not a test.
    """
    from publish.rewrite import RewriteOutcome

    monkeypatch.setattr(
        "publish.store.rewrite",
        lambda sections, **kw: RewriteOutcome(sections, used_llm=False,
                                              reason="stubbed in tests"),
    )


@pytest.fixture
def client(db, monkeypatch):
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
    ("get", "/api/publish/reports"),
    ("post", "/api/publish/reports"),
    ("get", "/api/publish/reports/rep-x"),
    ("delete", "/api/publish/reports/rep-x"),
    ("patch", "/api/publish/sections/sec-x"),
    ("post", "/api/publish/sections/sec-x/approve"),
    ("post", "/api/publish/reports/rep-x/approve"),
    ("get", "/api/publish/reports/rep-x/export"),
    ("get", "/api/publish/quarters"),
])
def test_every_endpoint_requires_sign_in(anon, method, path):
    assert getattr(anon, method)(path).status_code == 401


def test_there_is_no_endpoint_that_distributes_externally():
    """§8.3: no automated action may publish externally available content.

    This is the test that stops a well-meaning future change from adding one.
    """
    from api.server import app

    forbidden = ("send", "distribute", "hubspot", "email", "publish-to", "broadcast")
    offenders = [
        r.path for r in app.routes
        if any(word in str(getattr(r, "path", "")).lower() for word in forbidden)
    ]
    assert offenders == [], f"external-send endpoint(s) added: {offenders}"


def test_generate_then_read_back(client, db):
    _signal(db, "s1")
    created = client.post("/api/publish/reports", json={"quarter": QUARTER})
    assert created.status_code == 201
    rid = created.json()["id"]
    assert client.get(f"/api/publish/reports/{rid}").json()["quarter"] == QUARTER
    assert [r["id"] for r in client.get("/api/publish/reports").json()["reports"]] == [rid]


def test_the_approver_comes_from_the_session_not_the_request(client, db):
    """A client that can name its own approver has not been approved by anyone."""
    _signal(db, "s1")
    rid = client.post("/api/publish/reports", json={"quarter": QUARTER}).json()["id"]
    sec = client.get(f"/api/publish/reports/{rid}").json()["sections"][0]

    r = client.post(f"/api/publish/sections/{sec['id']}/approve",
                    json={"approvedBy": "someone.else@example.com"}).json()
    approved = next(s for s in r["sections"] if s["id"] == sec["id"])
    assert approved["approvedBy"] != "someone.else@example.com"
    assert "@" in (approved["approvedBy"] or "")


def test_approving_early_is_a_400_with_the_reason(client, db):
    _signal(db, "s1")
    rid = client.post("/api/publish/reports", json={"quarter": QUARTER}).json()["id"]
    res = client.post(f"/api/publish/reports/{rid}/approve")
    assert res.status_code == 400
    assert "Every section must be approved" in res.json()["detail"]


def test_export_serves_markdown_and_html(client, db):
    _signal(db, "s1")
    rid = client.post("/api/publish/reports", json={"quarter": QUARTER}).json()["id"]

    md = client.get(f"/api/publish/reports/{rid}/export?format=md")
    assert md.status_code == 200
    assert "text/markdown" in md.headers["content-type"]
    assert "attachment" in md.headers["content-disposition"]

    html = client.get(f"/api/publish/reports/{rid}/export?format=html")
    assert "text/html" in html.headers["content-type"]
    assert "<h1>" in html.text
    assert "DRAFT" in html.text


def test_an_unknown_export_format_is_refused(client, db):
    _signal(db, "s1")
    rid = client.post("/api/publish/reports", json={"quarter": QUARTER}).json()["id"]
    assert client.get(f"/api/publish/reports/{rid}/export?format=pdf").status_code == 422


def test_quarters_lists_what_the_data_holds(client, db):
    _signal(db, "s1", when="2026-08-15T10:00:00+00:00")
    _signal(db, "s2", when="2026-02-10T10:00:00+00:00")
    quarters = client.get("/api/publish/quarters").json()["quarters"]
    assert "2026-Q3" in quarters and "2026-Q1" in quarters


def test_a_missing_report_is_a_404(client):
    assert client.get("/api/publish/reports/rep-nope").status_code == 404
    assert client.delete("/api/publish/reports/rep-nope").status_code == 404


def test_deleting_a_report_removes_its_sections_too(client, db):
    _signal(db, "s1")
    rid = client.post("/api/publish/reports", json={"quarter": QUARTER}).json()["id"]
    assert client.delete(f"/api/publish/reports/{rid}").json()["deleted"] == rid

    with connect(db) as conn:
        left = conn.execute(
            "SELECT count(*) FROM report_sections WHERE report_id = ?", (rid,)
        ).fetchone()[0]
    assert left == 0, "orphaned sections left behind"


def test_an_oversized_section_is_refused(client, db):
    _signal(db, "s1")
    rid = client.post("/api/publish/reports", json={"quarter": QUARTER}).json()["id"]
    sec = client.get(f"/api/publish/reports/{rid}").json()["sections"][0]
    res = client.patch(f"/api/publish/sections/{sec['id']}", json={"body": "x" * 20_001})
    assert res.status_code == 400
    assert "character limit" in res.json()["detail"]


def test_the_full_review_journey(client, db):
    """Generate, write the outlook, approve each section, sign off, export."""
    for i in range(15):
        _signal(db, f"s{i}")

    rid = client.post("/api/publish/reports", json={"quarter": QUARTER}).json()["id"]
    report = client.get(f"/api/publish/reports/{rid}").json()

    for s in report["sections"]:
        if s["source"] == "manual":
            client.patch(f"/api/publish/sections/{s['id']}",
                         json={"body": "Demand is expected to hold into Q4."})
    for s in client.get(f"/api/publish/reports/{rid}").json()["sections"]:
        client.post(f"/api/publish/sections/{s['id']}/approve")

    final = client.post(f"/api/publish/reports/{rid}/approve")
    assert final.status_code == 200
    assert final.json()["status"] == "approved"

    md = client.get(f"/api/publish/reports/{rid}/export?format=md").text
    assert "DRAFT" not in md
    assert "Demand is expected to hold into Q4." in md
