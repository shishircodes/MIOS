"""The contract between the scorer and the screen that defends its scores.

The detail drawer exists to let a consultant argue with a number before putting
a company in front of a client. It can only do that if the payload carries the
working — every contributor including the ones that could not be judged, why
each was not, and which of the candidate's claimed skills the adverts actually
asked for.

That is a contract between two halves of the codebase that a type-check cannot
see across: the API is Python, the drawer is TypeScript, and a field quietly
renamed on one side shows up as an empty panel on the other. So these assert the
shape the interface reads, by name.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest

from config.settings import settings as real_settings
from loader.db import connect
from loader.ingest import init_db
from push.matcher import CONTRIBUTOR_LABEL, match_profile
from push.rarity import none_for

PROFILE = {
    "id": "p-1",
    "fullName": "Mark Anderson",
    "currentTitle": "Maintenance Planner",
    "sector": "mining",
    "region": "AU",
    "yearsExperience": 12,
    "skills": ["SAP", "shutdown planning", "HAZOP"],
}


def signal(company="BHP", title="Senior Maintenance Planner",
           body="SAP PM and shutdown planning", days_ago=1, tier="A"):
    captured = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(
        timespec="seconds")
    return {
        "company_name": company,
        "raw_content": f"{title} | {company} | {body}",
        "sector": "mining",
        "geography": "AU",
        "watchlist_tier": tier,
        "captured_at": captured,
        "signal_category": "hiring_velocity",
    }


def one_match(profile=None, signals=None):
    results = match_profile(profile or PROFILE, signals or [signal(), signal()],
                            rarity_model=none_for())
    assert results, "the fixture should produce at least one company"
    return results[0].to_dict(rank=1)


# ---------- the working is in the payload ----------


def test_every_contributor_is_present_including_the_unjudged_ones(db=None):
    """The drawer shows what the score does not cover. Sending only the ones
    that scored would let a company judged on a third of the model look as
    though it had been judged on all of it."""
    payload = one_match()
    keys = {c["key"] for c in payload["contributions"]}

    assert keys == set(CONTRIBUTOR_LABEL)


def test_an_unjudged_contributor_says_why_in_words_a_reader_can_act_on():
    """"Not assessed" tells nobody anything. "No skills recorded on the profile"
    tells them to go and add some."""
    bare = {**PROFILE, "skills": [], "currentTitle": None}
    payload = one_match(profile=bare)

    skills = next(c for c in payload["contributions"] if c["key"] == "skills")
    assert skills["earned"] is None
    assert skills["unassessedBecause"]
    assert "skills" in skills["unassessedBecause"].lower()


def test_an_unjudged_contributor_has_no_bar_rather_than_a_bar_of_zero():
    """A zero-length bar says it scored badly. It was never marked, and that is
    the distinction the whole normalisation rests on."""
    bare = {**PROFILE, "skills": []}
    payload = one_match(profile=bare)

    skills = next(c for c in payload["contributions"] if c["key"] == "skills")
    assert skills["share"] is None


def test_each_contributor_carries_its_weight_and_what_it_asks():
    payload = one_match()

    for c in payload["contributions"]:
        assert c["weight"] > 0, f"{c['key']} has no weight"
        assert c["label"], f"{c['key']} has no label"
        assert c["asks"], f"{c['key']} does not say what it asks"


def test_the_contributor_weights_still_sum_to_one_hundred():
    """The drawer sizes each bar by its share of the model. If these do not sum
    to 100 the bars are drawn against a total that does not exist."""
    payload = one_match()

    assert sum(c["weight"] for c in payload["contributions"]) == 100


# ---------- skills, shown in full ----------


def test_claimed_skills_appear_whether_or_not_they_matched():
    """Showing only the hits would let one match out of six read as a strong
    overlap."""
    payload = one_match()
    names = {s["name"]: s["matched"] for s in payload["skillDetail"]}

    assert names["SAP"] is True
    assert names["Shutdown Planning"] is True
    assert names["HAZOP"] is False


def test_each_skill_says_what_kind_it_is_and_how_common():
    payload = one_match()

    for s in payload["skillDetail"]:
        assert s["kindLabel"], f"{s['name']} has no kind label"
        assert s["rarity"], f"{s['name']} does not say how common it is"


# ---------- the ranking still behaves ----------


def test_the_score_is_the_earned_over_assessable_scaled():
    payload = one_match()

    expected = round(payload["earned"] / payload["assessable"] * 100)
    assert payload["score"] == min(100, expected)


def test_assessable_is_the_sum_of_the_judged_contributors():
    """The number on the screen next to "assessed" has to be the same arithmetic
    the drawer's bars add up to, or the two contradict each other."""
    payload = one_match()

    judged = sum(c["weight"] for c in payload["contributions"] if c["earned"] is not None)
    assert payload["assessable"] == judged


def test_earned_is_the_sum_of_what_the_contributors_earned():
    payload = one_match()

    assert payload["earned"] == sum(
        c["earned"] for c in payload["contributions"] if c["earned"] is not None)


def test_the_payload_is_json_serialisable():
    """It crosses an HTTP boundary; a dataclass left in it would 500 at
    render time rather than here."""
    json.dumps(one_match())
