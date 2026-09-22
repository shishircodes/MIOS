"""The full-length quarterly report.

The report grew from seven sections to twenty-one, sized like the quarterly
labour-market reports it sits beside. What is pinned here is that the extra
length is all counted — nothing padded, nothing estimated — and that the parts
added for a long document (tables, comparisons, placing ads by state and
province, the printable layout) behave.
"""
from __future__ import annotations

import json

import pytest

from loader.db import connect
from loader.ingest import init_db
from publish.report import Section, _province_in, _state_in, generate
from publish.rewrite import rewrite, split_narrative

QUARTER = "2026-Q3"


@pytest.fixture
def db(tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    path = tmp_path / "full.db"
    init_db(path, watchlist_path=wl)
    return path


_n = [0]


def _add(db, *, company="BHP", sector="mining", geo="AU", raw=None, kind="job_board",
         source="seek", category="hiring_velocity", when="2026-08-15T10:00:00+00:00",
         tier=None, new=0):
    _n[0] += 1
    sid = f"s{_n[0]}"
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, captured_at, "
            "geography, sector, company_name, watchlist_tier, signal_category, review_cycle, "
            "raw_content, analysis_notes, is_new_prospect, classified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, kind, source, f"https://x/{sid}", when, geo, sector, company, tier, category,
             "quarterly", raw or f"Maintenance Planner | {company} | Newman WA", "n", new, when),
        )


def _body(db, heading):
    return next(s.body for s in generate(QUARTER, db)[1] if s.heading == heading)


def test_signals_outside_the_sectors_are_left_out_and_counted(db):
    _add(db)
    _add(db, company="PNG Air", sector="other", geo="PNG")
    data, sections = generate(QUARTER, db)
    assert data.total == 1
    assert data.not_relevant == 1
    method = next(s.body for s in sections if s.heading == "Methodology")
    assert "left out of every figure: 1 this quarter" in method


def test_key_figures_compare_with_the_previous_quarter(db):
    for _ in range(4):
        _add(db)
    _add(db, when="2026-05-10T10:00:00+00:00")   # Q2
    _add(db, when="2026-05-11T10:00:00+00:00")
    body = _body(db, "Key Figures")
    assert "compared with 2026-Q2" in body
    assert "| Signals analysed | 4 | 2 | +100% |" in body


def test_with_no_earlier_quarter_there_is_no_change_to_show(db):
    _add(db)
    body = _body(db, "Key Figures")
    assert "no earlier quarter on record" in body
    assert "| Signals analysed | 1 | — | — |" in body


@pytest.mark.parametrize("header,state", [
    ("Maintenance Planner | BHP | Newman WA", "WA"),
    ("Site Engineer | Downer | Brisbane QLD", "QLD"),
    ("Fitter | BHP | Olympic Dam", "SA"),
    ("Electrician | Ausgrid | Sydney", "NSW"),
    ("Planner | BHP | Somewhere remote", None),
])
def test_ads_are_placed_by_state_from_their_location(header, state):
    assert _state_in(header) == state


@pytest.mark.parametrize("header,province", [
    ("Operator | K92 Mining | Eastern Highlands", "Eastern Highlands"),
    ("Accountant | PNG Air | National Capital District", "National Capital District"),
    ("Driller | Newmont | Lihir", "New Ireland"),
    ("Clerk | Trukai | Lae", "Morobe"),
    ("Planner | Somebody | Nowhere", None),
])
def test_png_ads_are_placed_by_province(header, province):
    assert _province_in(header) == province


def test_the_state_table_counts_what_it_can_place(db):
    _add(db, raw="Planner | BHP | Newman WA")
    _add(db, raw="Planner | BHP | Perth")
    _add(db, raw="Planner | BHP | Remote site")
    body = _body(db, "Australia by State")
    assert "| Western Australia | 2 |" in body
    assert "| Not stated | 1 |" in body


def test_new_prospects_exclude_agencies_and_tender_buyers(db):
    _add(db, company="Durack Civil", sector="construction", new=1)
    _add(db, company="Allstar Recruitment Group", new=1)
    body = _body(db, "New Prospects")
    assert "Durack Civil" in body
    assert "Allstar" not in body


def test_agency_ads_are_reported_as_competitor_activity(db):
    _add(db, company="Allstar Recruitment Group")
    _add(db, company="BHP")
    body = _body(db, "Competitor Activity")
    assert "| Allstar Recruitment Group | 1 | Mining |" in body
    assert "BHP" not in body


def test_tenders_are_listed_with_their_agency_and_closing_date(db):
    _add(db, kind="tender", source="austender", company="Department of Defence",
         sector="defence", category="project",
         raw="Fire services upgrade | Department of Defence | Building | closes 28-Sep-2026")
    body = _body(db, "Projects, Investment and Tenders")
    assert "Department of Defence" in body
    assert "28-Sep-2026" in body


def test_sector_chapters_list_notable_news(db):
    _add(db, kind="news", source="newsfeed", category="project", company="Lindian Resources",
         raw="Lindian secures feedstock for its facility | Mining.com.au | detail")
    body = _body(db, "Mining")
    assert "Notable developments" in body
    assert "Mining.com.au · Lindian secures feedstock" in body


def test_a_realistic_quarter_fills_the_report(db):
    """Sized like a quarterly labour-market report: every chapter carries data
    when the data is there, rather than the one-line placeholder."""
    sectors = ["mining", "oil_gas", "construction", "defence", "energy_transition"]
    for i in range(60):
        _add(db, company=f"Employer {i % 12}", sector=sectors[i % 5],
             geo="PNG" if i % 4 == 0 else "AU",
             raw=f"Maintenance Planner | Employer {i % 12} | "
                 f"{'Eastern Highlands' if i % 4 == 0 else 'Perth WA'}",
             when=f"2026-0{7 + i % 3}-1{i % 9}T10:00:00+00:00", new=i % 2)
    _data, sections = generate(QUARTER, db)
    text = "\n\n".join(s.body for s in sections)
    assert len(sections) == 21
    assert text.count("\n| ") > 100, "tables carry the figures"
    assert len(text.split()) > 1500


def test_the_rewrite_only_touches_the_prose(db):
    computed = [Section("Mining",
                        "MIOS detected 12 signals.\n\n### Most active employers\n\n"
                        "| Employer | Signals |\n|---|---|\n| BHP | 12 |")]

    sent = {}

    def fake(system, prompt, schema=None, **kw):
        sent["prompt"] = prompt
        return {"sections": [{"heading": "Mining", "body": "The quarter saw 12 signals."}]}

    out = rewrite(computed, target=db, gemini_caller=fake)
    assert "| BHP |" not in sent["prompt"], "tables are never sent to the model"
    assert out.sections[0].body.startswith("The quarter saw 12 signals.")
    assert "| BHP | 12 |" in out.sections[0].body, "and come back exactly as computed"


def test_split_narrative_keeps_a_framing_line_with_its_table():
    narrative, rest = split_narrative("Intro.\n\nThe most active were:\n\n| A |\n|---|\n| x |")
    assert narrative == "Intro."
    assert rest.startswith("The most active were:")


def test_the_printable_export_has_a_cover_contents_and_real_tables(db):
    from api.publish_api import _to_html

    report = {"title": "Quarterly Market Report 2026-Q3", "quarter": QUARTER, "status": "draft",
              "signalsAnalysed": 12, "approvedBy": None, "approvedAt": None,
              "sections": [{"heading": "Mining",
                            "body": "Intro.\n\n| Employer | Signals |\n|---|---|\n| BHP | 12 |"},
                           {"heading": "Methodology", "body": "- one\n- two"}]}
    html = _to_html(report, "")
    assert 'class="cover"' in html and 'class="contents"' in html
    assert "<table>" in html and "<td>BHP</td>" in html
    assert "<ul><li>one</li>" in html
    assert 'class="chapter"' in html and 'class="same-page"' in html
