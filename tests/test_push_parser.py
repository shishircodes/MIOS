"""Tests for CV extraction and rule-based profile parsing (push.cv_extract,
push.profile_parser). No LLM, no network — all deterministic."""
from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from push.cv_extract import CVExtractionError, extract_text
from push.profile_parser import parse_profile

CV_TEXT = """MARK ANDERSON
Senior Maintenance Planner
mark.anderson@example.com  |  +61 412 555 019  |  Perth, Western Australia

PROFESSIONAL SUMMARY
Maintenance Planner with 12 years experience across iron ore and gold mining
operations in the Pilbara. Strong shutdown planning background, SAP PM and Primavera P6.

EMPLOYMENT HISTORY
2019 - Present   Maintenance Planner, BHP Iron Ore, Newman WA
2014 - 2019      Maintenance Supervisor, Rio Tinto, Karratha WA

SKILLS
SAP, Primavera P6, RCM, shutdown planning, confined space, white card
"""


def _make_docx(paragraphs: list[str]) -> bytes:
    """Minimal but structurally valid .docx."""
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


# ---------- .docx extraction ----------


def test_extracts_text_from_docx():
    data = _make_docx(CV_TEXT.split("\n"))
    text = extract_text(data, "mark_anderson.docx")
    assert "MARK ANDERSON" in text
    assert "Primavera P6" in text


def test_docx_preserves_line_structure():
    """The name sits on its own line — collapsing paragraphs would lose it."""
    data = _make_docx(["JANE SMITH", "Reliability Engineer", "jane@example.com"])
    lines = extract_text(data, "cv.docx").split("\n")
    assert lines[0] == "JANE SMITH"


def test_docx_unescapes_xml_entities():
    data = _make_docx(["ACME &amp; CO", "Oil &amp; Gas Superintendent with 9 years experience"])
    assert "ACME & CO" in extract_text(data, "cv.docx")


def test_corrupt_docx_gives_a_usable_message():
    with pytest.raises(CVExtractionError, match="not a valid .docx"):
        extract_text(b"this is not a zip file at all, just plain bytes here", "cv.docx")


def test_legacy_doc_is_refused_explicitly():
    """.doc is a different container; guessing would produce mojibake."""
    with pytest.raises(CVExtractionError, match="Save As"):
        extract_text(b"\xd0\xcf\x11\xe0" + b"x" * 100, "cv.doc")


# ---------- guards ----------


def test_empty_file_is_refused():
    with pytest.raises(CVExtractionError, match="empty"):
        extract_text(b"", "cv.docx")


def test_oversized_file_is_refused():
    with pytest.raises(CVExtractionError, match="limit is"):
        extract_text(b"x" * (11 * 1024 * 1024), "cv.pdf")


def test_unsupported_extension_is_refused():
    with pytest.raises(CVExtractionError, match="Unsupported file type"):
        extract_text(b"some text content here for the CV", "cv.txt")


def test_near_empty_document_is_refused():
    with pytest.raises(CVExtractionError, match="almost no readable text"):
        extract_text(_make_docx(["Hi"]), "cv.docx")


# ---------- parsing ----------


@pytest.fixture
def parsed():
    return parse_profile(CV_TEXT)


def test_extracts_name_from_the_top_of_the_cv(parsed):
    assert parsed.full_name == "Mark Anderson"
    assert parsed.confidence["full_name"] == "high"


def test_extracts_contact_details(parsed):
    assert parsed.email == "mark.anderson@example.com"
    assert parsed.phone and "412" in parsed.phone


def test_extracts_role_sector_and_region(parsed):
    assert parsed.current_title == "Maintenance Planner"
    assert parsed.sector == "mining"
    assert parsed.region == "AU"


def test_prefers_a_stated_years_figure(parsed):
    assert parsed.years_experience == 12


def test_infers_years_from_date_ranges_when_not_stated():
    p = parse_profile(
        "JOHN CITIZEN\nProcess Engineer\nEMPLOYMENT\n2010 - 2018 Process Engineer, "
        "LNG plant\n2018 - Present Senior Process Engineer, refinery operations"
    )
    assert p.years_experience is not None and p.years_experience >= 10
    assert p.confidence["years_experience"] == "medium"


def test_extracts_skills(parsed):
    assert "sap" in parsed.skills
    assert "shutdown planning" in parsed.skills


def test_png_cv_is_detected_as_png():
    p = parse_profile(
        "PETER KAMU\nProcess Operator\nLihir gold mine, Papua New Guinea. "
        "8 years experience at Ok Tedi and Porgera."
    )
    assert p.region == "PNG"


def test_oil_and_gas_beats_the_broader_mining_vocabulary():
    p = parse_profile(
        "SAM LEE\nOffshore Superintendent\nLNG and upstream oil and gas experience, "
        "FPSO and refinery turnarounds, wellhead maintenance."
    )
    assert p.sector == "oil_gas"


def test_section_headings_are_not_mistaken_for_names():
    p = parse_profile("CURRICULUM VITAE\nProfessional Summary\nJane Doe\nSafety Manager")
    assert p.full_name != "Curriculum Vitae"


def test_parsing_never_raises_on_junk():
    """The parser returns a low-confidence draft rather than failing — a human
    reviews it either way."""
    p = parse_profile("!!!! ???? ....")
    assert p.full_name is None or isinstance(p.full_name, str)
    assert p.to_dict()["confidence"]["full_name"] == "low"


def test_empty_text_returns_an_empty_draft():
    p = parse_profile("")
    assert p.full_name is None
    assert p.skills == []


def test_confidence_is_reported_for_every_field(parsed):
    expected = {"full_name", "email", "phone", "current_title",
                "sector", "years_experience", "region", "skills"}
    assert expected <= set(parsed.confidence)
    assert set(parsed.confidence.values()) <= {"high", "medium", "low"}
