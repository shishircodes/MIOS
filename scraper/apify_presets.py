"""Default searches for the Apify actors MIOS knows.

An actor run with no input uses its author's defaults, which for a job board
means every kind of job: the first production run of SEEK and Jora (5 Oct 2026)
brought back dental assistants, pick packers and dog walkers, and the
classifier filed 66 of 70 under "other".

Every actor names its input differently, so a default search cannot be written
once for all of them. It is written here per actor, against the actor's own
published input schema, and limited to the sectors Easy Skill recruits into:
mining, oil and gas, energy, construction and defence.

**A default, not a rule.** It applies only while an administrator has entered
no search settings for the board. What they enter under Admin › Integrations
replaces it entirely, and clearing that returns to it.

An actor that is not listed here has no default: its field names are not known,
and guessing them would send an input the actor ignores. The panel says so.

Field names and allowed values were read from each actor's input schema on the
Apify Store on 5 Oct 2026. An actor's author can change them; if a default
stops narrowing the results, that is the first thing to check.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Preset:
    #: The board this actor reads, as its id in the catalogue.
    board: str
    #: The search, in the actor's own field names.
    input: dict[str, Any] = field(default_factory=dict)
    #: The actor's own name for "how many results". The board's limit is put
    #: there on every run, so the actor stops instead of running to a cap.
    count_field: str | None = None
    #: The most the actor accepts in that field, where it sets one.
    max_count: int | None = None


PRESETS: dict[str, Preset] = {
    # SEEK files every advert under a classification, so the search is by
    # classification rather than by keyword: nothing depends on the wording of
    # a title. "Mining, Resources & Energy" covers mining, oil and gas and power
    # generation. SEEK has no defence-industry classification; the three
    # services are the nearest it has.
    "websift/seek-job-scraper": Preset(
        board="seek",
        input={
            "mining-resources-energy": True,
            "construction": True,
            "air-force": True,
            "army": True,
            "navy": True,
            "country": "australia",
            "sortBy": "ListedDate",
            # A run is weekly, so a week of adverts is what is new.
            "dateRange": 7,
        },
        count_field="maxResults",
        max_count=550,
    ),
    # This actor takes one keyword and has no category filter, so the default
    # is the sector most of the watchlist is in. Whether Jora reads "OR"
    # between keywords could not be checked, so it is not relied on.
    "shahidirfan/jora-jobs-scraper": Preset(
        board="jora",
        input={
            "keyword": "mining",
            "country": "Australia",
            "posted_date": "7d",
        },
        count_field="results_wanted",
    ),
}


def _key(actor: str) -> str:
    # Apify treats names case-insensitively and writes user/actor as user~actor
    # in addresses; an administrator may paste either.
    return (actor or "").strip().lower().replace("~", "/")


def preset_for(actor: str) -> Preset | None:
    return PRESETS.get(_key(actor))


def default_input(actor: str) -> dict[str, Any]:
    """The default search for an actor, or {} when MIOS has none for it."""
    preset = preset_for(actor)
    return dict(preset.input) if preset else {}


def known_actors(board: str) -> list[str]:
    """The actors with a default search for a board."""
    return [name for name, p in PRESETS.items() if p.board == board]
