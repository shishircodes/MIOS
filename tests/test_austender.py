"""AusTender approaches to market.

The point of this source is that its relevance is decided by the publisher.
AusTender categorises every notice from a UNSPSC-derived vocabulary, so keeping
the construction and engineering work is a string match rather than a judgement
— which is why this source costs no model calls. Most of these are therefore
about that filter being right, because a filter that is wrong here either spends
the allowance this source was meant to save or silently drops the work.
"""
from __future__ import annotations

from scraper.austender import (
    ATM_PATH,
    is_relevant,
    parse_listing,
)

ROW = """
<div class="box boxW listInner">
  <div class="list-desc"><span>ATM ID:</span><div class="list-desc-inner">{atm}</div></div>
  <div class="list-desc"><span>Close Date &amp; Time:</span>
    <div class="list-desc-inner">{closes}</div></div>
  <div class="list-desc"><span>Agency:</span><div class="list-desc-inner">{agency}</div></div>
  <div class="list-desc"><span>Category:</span><div class="list-desc-inner">{category}</div></div>
  <div class="list-desc"><span>Description:</span><div class="list-desc-inner">{desc}</div></div>
  <div class="list-desc"><a href="/Atm/Show/{atm}">Full Details</a></div>
  <div class="last-updated"><strong>Last Updated:</strong> {updated}</div>
</div>
"""


def listing(*rows: str) -> str:
    return "<html><body>" + "".join(rows) + "</body></html>"


def row(atm="ABC123", agency="Department of Defence - DSRG",
        category="Building construction and support and maintenance and repair services",
        desc="RAAF Edinburgh Infrastructure Upgrade",
        closes="29-Sep-2026 12:00 pm", updated="3-Sep-2026 12:32 pm") -> str:
    return ROW.format(atm=atm, agency=agency, category=category, desc=desc,
                      closes=closes, updated=updated)


# ---------- the filter is the source ----------


def test_construction_and_engineering_are_kept():
    assert is_relevant("Building construction and support and maintenance and repair services")
    assert is_relevant("Professional engineering services")
    assert is_relevant("Military services and national defence")


def test_unrelated_procurement_is_dropped():
    """AusTender carries all Commonwealth procurement. On the list sampled while
    writing this, crop production was the second largest category."""
    assert not is_relevant("Crop production and management and protection")
    assert not is_relevant("Software as a Service (SaaS - Cloud)")
    assert not is_relevant("Office furniture")
    assert not is_relevant("Interpreters")


def test_an_exclusion_beats_a_relevant_word():
    """Both of these were observed on the live list and both carry a word from
    the relevant set — "building" and "maintenance" respectively. Neither is
    work Easy Skill recruits for."""
    assert not is_relevant("General building and office cleaning and maintenance services")
    assert not is_relevant("Computer hardware maintenance and support")


def test_an_empty_category_is_not_relevant():
    """Better to drop a notice with no category than to let an unlabelled one
    through on the assumption it might qualify."""
    assert not is_relevant("")
    assert not is_relevant(None)  # type: ignore[arg-type]


# ---------- reading the list ----------


def test_a_relevant_notice_becomes_a_record():
    records = parse_listing(listing(row()))

    assert len(records) == 1
    rec = records[0]
    assert rec["title"] == "RAAF Edinburgh Infrastructure Upgrade"
    assert rec["agency"] == "Department of Defence - DSRG"
    assert rec["source_url"].endswith("/Atm/Show/ABC123")
    assert rec["source_name"] == "austender"
    assert rec["geography"] == "AU"


def test_the_agency_and_category_travel_with_the_signal():
    """An approach to market is only intelligence once you know who is
    approaching and for what."""
    rec = parse_listing(listing(row()))[0]

    assert "Department of Defence" in rec["raw_content"]
    assert "Building construction" in rec["raw_content"]
    assert "closes" in rec["raw_content"]


def test_an_irrelevant_notice_never_becomes_a_record():
    """Dropped during parsing, so it never reaches the classifier. Passing
    office furniture to a model to be rejected would spend the allowance this
    source exists to save."""
    html = listing(row(atm="A1"), row(atm="B2", category="Office furniture",
                                      desc="Task chairs"))

    records = parse_listing(html)

    assert [r["source_url"].split("/")[-1] for r in records] == ["A1"]


def test_fields_are_read_by_label_not_by_position():
    """A reordered or newly inserted field costs nothing; an index would shift
    every value by one and do it silently."""
    reordered = ROW.replace(
        '<div class="list-desc"><span>ATM ID:</span><div class="list-desc-inner">{atm}</div></div>',
        '<div class="list-desc"><span>New Field:</span><div class="list-desc-inner">x</div></div>'
        '<div class="list-desc"><span>ATM ID:</span><div class="list-desc-inner">{atm}</div></div>',
    ).format(atm="Z9", agency="Ag", category="Professional engineering services",
             desc="A bridge", closes="1-Oct-2026", updated="1-Sep-2026")

    rec = parse_listing(listing(reordered))[0]

    assert rec["title"] == "A bridge"
    assert rec["agency"] == "Ag"


def test_a_row_without_a_link_is_skipped():
    """Without a URL the record cannot be deduplicated or followed."""
    no_link = row().replace('<a href="/Atm/Show/ABC123">Full Details</a>', "Full Details")

    assert parse_listing(listing(no_link)) == []


def test_markup_with_no_rows_yields_nothing_rather_than_failing():
    assert parse_listing("<html><body><p>No results</p></body></html>") == []


def test_only_the_permitted_path_is_read():
    """robots.txt disallows /Search/*, /Reports/*, /Cn/List* and /Son/List*.
    The contract-notice lists carry similar information and are deliberately
    left alone."""
    assert ATM_PATH == "/Atm"
