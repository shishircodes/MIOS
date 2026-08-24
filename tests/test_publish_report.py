"""Tests for report assembly (publish.report).

The prose is generated deterministically from counts, so every claim here can be
checked exactly. The point of most of these is that the report does not overstate
what the data supports — that failure mode has already cost this project once,
in the Hiring Velocity table.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from loader.db import connect
from loader.ingest import init_db
from publish.report import (
    MIN_SIGNALS_FOR_PERCENTAGES,
    current_quarter,
    gather,
    generate,
    quarter_bounds,
    quarter_of,
)


@pytest.fixture
def watchlist(tmp_path):
    p = tmp_path / "wl.json"
    p.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "", "aliases": []},
    ]))
    return p


@pytest.fixture
def db(tmp_path, watchlist):
    path = tmp_path / "publish.db"
    init_db(path, watchlist_path=watchlist)
    return path


QUARTER = "2026-Q3"


def _add(db, signal_id, *, company="BHP", sector="mining", geo="AU",
         raw=None, when="2026-08-15T10:00:00+00:00", source="seek"):
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, sector, company_name, watchlist_tier, "
            "signal_category, review_cycle, raw_content, analysis_notes, "
            "is_new_prospect, classified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, "job_board", source, f"https://x/{signal_id}", when, geo,
             sector, company, None, "hiring_velocity", "quarterly",
             raw or f"Maintenance Planner | {company} | Newman WA", "note", 0, when),
        )


def _text(db, heading):
    _data, sections = generate(QUARTER, db)
    return next(s.body for s in sections if s.heading == heading)


# ---------- quarters ----------


def test_quarter_of_maps_months_to_quarters():
    assert quarter_of(datetime(2026, 1, 5, tzinfo=timezone.utc)) == "2026-Q1"
    assert quarter_of(datetime(2026, 3, 31, tzinfo=timezone.utc)) == "2026-Q1"
    assert quarter_of(datetime(2026, 8, 15, tzinfo=timezone.utc)) == "2026-Q3"
    assert quarter_of(datetime(2026, 12, 31, tzinfo=timezone.utc)) == "2026-Q4"


def test_quarter_bounds_are_half_open():
    start, end = quarter_bounds("2026-Q3")
    assert start == datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 1, tzinfo=timezone.utc), "exclusive end"


def test_q4_rolls_into_the_next_year():
    _start, end = quarter_bounds("2026-Q4")
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_a_bad_quarter_label_is_rejected():
    for bad in ("2026-Q5", "Q3-2026", "2026", "", "nonsense"):
        with pytest.raises(ValueError):
            quarter_bounds(bad)


def test_current_quarter_is_well_formed():
    assert quarter_bounds(current_quarter())


# ---------- gathering ----------


def test_only_the_quarters_signals_are_counted(db):
    _add(db, "in", when="2026-08-15T10:00:00+00:00")
    _add(db, "before", when="2026-06-30T23:00:00+00:00")
    _add(db, "after", when="2026-10-01T00:30:00+00:00")

    assert gather(QUARTER, db).total == 1


def test_unclassified_rows_are_excluded(db):
    _add(db, "done")
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, "
            "captured_at, geography, raw_content) VALUES (?,?,?,?,?,?,?)",
            ("raw", "job_board", "seek", "https://x/raw",
             "2026-08-15T10:00:00+00:00", "AU", "Planner"),
        )
    assert gather(QUARTER, db).total == 1


def test_region_uses_the_same_rule_as_the_dashboard(db):
    """A PNG role advertised on an Australian board counts as PNG here too,
    otherwise the report and the digest would disagree about the same row."""
    _add(db, "png-on-seek", geo="AU",
         raw="Project Engineers to work in PNG on civil projects | Kiwi Niugini")
    assert gather(QUARTER, db).by_region["PNG"] == 1


def test_counts_include_rows_with_no_named_employer(db):
    """Regression: the per-sector figure was derived from the company tally,
    which drops rows the classifier could not attribute — so the section
    under-reported its own total."""
    _add(db, "named", company="BHP")
    _add(db, "unknown", company="Unknown")

    data = gather(QUARTER, db)
    assert data.by_region_sector[("AU", "mining")] == 2
    assert set(data.companies[("AU", "mining")]) == {"BHP"}, "Unknown is not an employer"


def test_missing_database_yields_an_empty_quarter(tmp_path):
    assert gather(QUARTER, tmp_path / "nope.db").total == 0


# ---------- the prose ----------


def test_every_section_is_present(db):
    _add(db, "s1")
    _data, sections = generate(QUARTER, db)
    assert [s.heading for s in sections] == [
        "Executive Summary",
        "Australia — Mining",
        "Australia — Construction",
        "Papua New Guinea",
        "Skills Demand",
        "Looking Ahead",
        "Methodology",
    ]


def test_the_outlook_is_left_for_a_human(db):
    """"What happens next quarter" is a judgement, not a count. It ships empty
    and marked manual so nobody mistakes silence for analysis."""
    _add(db, "s1")
    _data, sections = generate(QUARTER, db)
    outlook = next(s for s in sections if s.heading == "Looking Ahead")
    assert outlook.body == ""
    assert outlook.source == "manual"


def test_every_other_section_is_generated(db):
    _add(db, "s1")
    _data, sections = generate(QUARTER, db)
    generated = [s.heading for s in sections if s.source == "generated"]
    assert len(generated) == 6


def test_percentages_are_withheld_when_the_sample_is_thin(db):
    """Three signals is not a trend, and "67% of activity" from two rows reads
    far stronger than it is."""
    for i in range(3):
        _add(db, f"s{i}")
    summary = _text(db, "Executive Summary")
    assert "%" not in summary
    assert "too low to express as meaningful percentages" in summary


def test_percentages_appear_once_there_is_enough_data(db):
    for i in range(MIN_SIGNALS_FOR_PERCENTAGES):
        _add(db, f"s{i}")
    assert "%" in _text(db, "Executive Summary")


def test_the_summary_counts_signals_and_sources(db):
    _add(db, "a", source="seek")
    _add(db, "b", source="adzuna")
    summary = _text(db, "Executive Summary")
    assert "2 hiring signals" in summary
    assert "2 sources" in summary


def test_company_names_carry_their_counts(db):
    """A bare list invites the reader to assume the names are comparable."""
    for i in range(5):
        _add(db, f"bhp{i}", company="BHP")
    _add(db, "tiny", company="Tiny Contractor")

    body = _text(db, "Australia — Mining")
    assert "BHP (5)" in body
    assert "Tiny Contractor (1)" in body


def test_unknown_employers_are_never_named(db):
    _add(db, "u1", company="Unknown")
    _add(db, "u2", company="unknown")
    body = _text(db, "Australia — Mining")
    assert "Unknown" not in body
    assert "2 hiring signals" in body, "but they still count towards the total"


def test_an_empty_sector_says_so_without_implying_a_downturn(db):
    _add(db, "s1", sector="mining")
    body = _text(db, "Australia — Construction")
    assert "No construction activity" in body
    assert "not evidence" in body, "absence of signal is not absence of hiring"


def test_no_png_data_blames_the_sources_not_the_market(db):
    _add(db, "au", geo="AU")
    body = _text(db, "Papua New Guinea")
    assert "PNGworkforce" in body
    assert "market slowdown" in body


def test_png_section_reports_its_own_sectors_and_employers(db):
    _add(db, "p1", geo="PNG", company="Newmont",
         raw="Process Operator at Lihir gold mine, Papua New Guinea")
    _add(db, "p2", geo="PNG", company="Newmont", sector="oil_gas",
         raw="Process Engineer at Port Moresby, Papua New Guinea")
    body = _text(db, "Papua New Guinea")
    assert "2 signals" in body
    assert "Newmont (2)" in body
    assert "Oil & Gas (1)" in body


def test_skills_section_counts_roles_and_says_what_it_excluded(db):
    _add(db, "r1", raw="Maintenance Planner | BHP | Newman")
    _add(db, "r2", raw="Maintenance Planner | BHP | Perth")
    _add(db, "r3", raw="Something with no recognised title at all here")

    body = _text(db, "Skills Demand")
    assert "Maintenance Planner — 2 postings" in body
    assert "2 of 3 postings" in body
    assert "understated rather than padded" in body


def test_methodology_names_the_sources_and_disclaims_estimation(db):
    _add(db, "a", source="seek")
    _add(db, "b", source="newsfeed")
    body = _text(db, "Methodology")
    assert "seek (1)" in body and "newsfeed (1)" in body
    assert "No figure in this report is estimated" in body
    assert "sample of the market, not a census" in body


def test_an_empty_quarter_reports_nothing_rather_than_inventing(db):
    summary = _text(db, "Executive Summary")
    assert "No signals were collected" in summary
    assert "%" not in summary


def test_generation_is_deterministic(db):
    """The same rows must always produce the same document — a client-facing
    report that changes wording between reads cannot be reviewed."""
    for i in range(12):
        _add(db, f"s{i}")
    first = [s.body for s in generate(QUARTER, db)[1]]
    second = [s.body for s in generate(QUARTER, db)[1]]
    assert first == second
