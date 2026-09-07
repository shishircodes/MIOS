"""Rarity weighting, and the taxonomy underneath it.

The old scorer counted every skill overlap equally, so a candidate and a company
both saying "lean" was worth what both saying "HAZOP" was worth. Nearly every
advert says lean; almost none say HAZOP. These pin the two properties that fixes
— that a rare match outweighs a common one, and that on a corpus too small to
measure, rarity does not apply at all rather than being guessed.
"""
from __future__ import annotations

from push import rarity, taxonomy
from push.matcher import _skills_overlap


def advert(text: str) -> dict:
    return {"raw_content": text, "company_name": "BHP"}


def corpus(common: int, rare: int, filler: int = 0) -> list[dict]:
    """`common` adverts mentioning lean, `rare` mentioning HAZOP."""
    rows = [advert(f"Planner {i} | lean improvement") for i in range(common)]
    rows += [advert(f"Engineer {i} | hazop facilitation") for i in range(rare)]
    rows += [advert(f"Operator {i} | general duties") for i in range(filler)]
    return rows


# ---------- the taxonomy ----------


def test_an_alias_matches_the_canonical_skill():
    """"P6" and "Primavera P6" are the same thing, and the old substring list
    scored a candidate lower for writing it the other way."""
    assert [s.name for s in taxonomy.find("experience with P6")] == ["Primavera P6"]
    assert [s.name for s in taxonomy.find("Oracle Primavera")] == ["Primavera P6"]


def test_the_longest_alias_wins():
    """"ms project" must not be read as a bare "project"."""
    found = [s.name for s in taxonomy.find("MS Project scheduling")]
    assert "MS Project" in found


def test_a_skill_does_not_match_inside_another_word():
    assert taxonomy.find("sapphire mine services") == []
    assert taxonomy.find("cleaning duties") == []


def test_canonicalising_a_cv_list_collapses_spellings():
    skills = taxonomy.canonicalise(["SAP PM", "sap", "Certificate IV"])
    assert [s.name for s in skills] == ["Cert IV", "SAP"]


def test_terms_the_vocabulary_cannot_place_are_reported_not_silently_dropped():
    """A skill the scorer cannot compare is a real loss, and the UI has to be
    able to say so rather than appear to have weighed it."""
    assert taxonomy.unknown(["SAP", "Underwater Basket Weaving"]) == \
        ["Underwater Basket Weaving"]


# ---------- rarity applies, or says it does not ----------


def test_a_small_corpus_does_not_get_weighted():
    """Document frequency over a handful of adverts is not a measurement. One
    advert mentioning a term makes it look rare when it is merely unobserved."""
    model = rarity.build(corpus(common=3, rare=1))

    assert model.applies is False
    assert model.of("HAZOP") == 1.0
    assert model.describe("HAZOP") == "not weighted"


def test_a_rare_term_outweighs_a_common_one():
    model = rarity.build(corpus(common=40, rare=2))

    assert model.applies is True
    assert model.of("HAZOP") > model.of("Lean")


def test_the_multiplier_is_capped():
    """Uncapped IDF lets one term appearing once dominate a whole score, which
    turns the model into a lottery on whichever word the scraper caught."""
    model = rarity.build(corpus(common=200, rare=1))

    assert model.of("HAZOP") <= rarity.MAX_MULTIPLIER
    assert model.of("Lean") >= rarity.MIN_MULTIPLIER


def test_weights_centre_on_one_so_scores_do_not_drift_with_corpus_size():
    """Without normalising, a score would move week to week because more adverts
    were collected, not because anything about the candidate or company changed."""
    small = rarity.build(corpus(common=20, rare=8, filler=10))
    large = rarity.build(corpus(common=200, rare=80, filler=100))

    assert abs(small.of("Lean") - large.of("Lean")) < 0.25


def test_an_unknown_term_weighs_average():
    model = rarity.build(corpus(common=40, rare=2))

    assert model.of("Something Not In The Vocabulary") == 1.0


# ---------- the scorer uses it ----------


def test_matching_a_rare_skill_scores_higher_than_matching_a_common_one():
    """The point of the whole exercise."""
    model = rarity.build(corpus(common=40, rare=2))

    rare_pts, _, _ = _skills_overlap(["HAZOP"], [advert("Engineer | hazop facilitation")],
                                     model)
    common_pts, _, _ = _skills_overlap(["Lean"], [advert("Planner | lean improvement")],
                                       model)

    assert rare_pts > common_pts


def test_the_evidence_line_names_a_rare_match():
    """A consultant reading one line needs the part that is unusual, not the
    first skill the candidate happened to type."""
    model = rarity.build(corpus(common=40, rare=2))

    _, ev, _ = _skills_overlap(["HAZOP"], [advert("Engineer | hazop facilitation")], model)

    assert "rare" in (ev or "").lower()


def test_skill_detail_reports_every_claimed_skill_matched_or_not():
    """The drawer shows what was looked for, not only what was found — a reader
    cannot tell a strong match from a thin one otherwise."""
    model = rarity.none_for()

    _, _, detail = _skills_overlap(["SAP", "HAZOP"], [advert("Planner | SAP only")], model)

    assert {d["name"]: d["matched"] for d in detail} == {"SAP": True, "HAZOP": False}
    assert all(d["kindLabel"] for d in detail)
