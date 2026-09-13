"""How much a match is worth, given how common the thing matched is.

The scorer treated every overlap as equal. A candidate and a company both
saying "lean" counted exactly as much as both saying "HAZOP" — but nearly every
advert says lean, and almost none say HAZOP. The first match separates nobody;
the second says these two belong in the same room. Weighting them identically
is the single largest thing the old model got wrong, because it is wrong on
every row rather than occasionally.

This is inverse document frequency, which is what search engines and applicant
tracking systems have used for this for decades. Nothing here is novel; it is
the standard answer to "how surprising is this term", applied to a corpus the
pipeline already collects.

    idf(term) = log(N / (1 + df(term))) + 1

`df` is the number of adverts mentioning the term, `N` the number of adverts.
The `+1` inside keeps a term appearing in every advert from reaching zero (it
should count for little, not nothing), and the `+1` outside keeps every weight
positive so a common match can never subtract.

**The corpus is the run's own signals, not a fixed table.** What is rare in
Papua New Guinean mining is not what is rare in Australian defence, and a
hardcoded rarity table would encode last quarter's market as though it were a
fact about the trade. Computing it per run costs one pass over signals already
in memory.

**Below a floor it does not apply at all.** Document frequency over eleven
adverts is not a measurement of anything — one advert mentioning a term makes
it look rare when it is merely unobserved. So under `MIN_CORPUS` every term
weighs the same and the scorer says so, which is the same discipline the
velocity baseline and the dashboard's percentage change already follow: a
number that cannot be measured is reported as absent, not estimated.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Iterable

from push import taxonomy
from push.taxonomy import Skill

log = logging.getLogger(__name__)

#: Fewer adverts than this and document frequency is noise. Twenty-five is not
#: a statistical threshold — nothing here is a hypothesis test — it is the point
#: below which a single advert moves a term's weight by more than a fifth.
MIN_CORPUS = 25

#: The most a rare term may outweigh an average one. Uncapped IDF on a small
#: corpus lets one term appearing once dominate a whole score, which turns a
#: scoring model into a lottery on whichever word the scraper happened to catch.
MAX_MULTIPLIER = 2.5
MIN_MULTIPLIER = 0.35

#: At or above this multiplier a term is worth calling out to the reader. One
#: constant so the score, the word in the breakdown and the evidence line cannot
#: disagree about which matches were the unusual ones.
RARE_AT = 1.5

#: How much of the skills weight a match of purely average rarity may earn.
#:
#: There has to be headroom above average or rarity cannot lift anything: with
#: no headroom, a candidate claiming one common skill and one claiming one rare
#: skill both match everything they claimed and both score full marks, which is
#: the flat model this module exists to replace. 0.8 leaves a fifth of the
#: weight reachable only by matching something the market rarely asks for.
AVERAGE_HEADROOM = 0.8


@dataclass
class Rarity:
    """Term weights for one corpus, and whether they mean anything.

    `applies` is False on a corpus too small to measure. Callers must not treat
    that as "everything is average" without saying so — the UI shows it, because
    a score computed with rarity and one computed without are different numbers
    and a reader comparing across weeks deserves to know which they have.
    """

    weights: dict[str, float]
    corpus_size: int
    applies: bool

    def of(self, skill: Skill | str) -> float:
        """The multiplier for this skill. 1.0 when rarity does not apply."""
        if not self.applies:
            return 1.0
        name = skill.name if isinstance(skill, Skill) else str(skill)
        return self.weights.get(name, 1.0)

    def describe(self, skill: Skill | str) -> str:
        """Plain words for how common this is, for the breakdown drawer."""
        if not self.applies:
            return "not weighted"
        m = self.of(skill)
        if m >= RARE_AT:
            return "rare in this market"
        if m >= 1.15:
            return "uncommon"
        if m <= 0.6:
            return "very common"
        if m <= 0.85:
            return "common"
        return "average"


def _advert_texts(signals: Iterable[dict[str, Any]]) -> list[str]:
    return [str(s.get("raw_content") or "") for s in signals if (s.get("raw_content") or "").strip()]


def build(signals: list[dict[str, Any]]) -> Rarity:
    """Weights for every vocabulary term, from this run's adverts.

    One pass. Each advert counts a term once however often it repeats it — an
    advert that says SAP eight times is one advert wanting SAP, and counting
    repetitions would let a verbose posting outvote three terse ones.
    """
    texts = _advert_texts(signals)
    n = len(texts)
    if n < MIN_CORPUS:
        log.info("rarity: corpus of %d adverts is below the floor of %d — "
                 "every term weighted equally", n, MIN_CORPUS)
        return Rarity(weights={}, corpus_size=n, applies=False)

    df: dict[str, int] = {}
    for text in texts:
        for skill in taxonomy.find(text):
            df[skill.name] = df.get(skill.name, 0) + 1

    weights: dict[str, float] = {}
    if df:
        raw = {name: math.log(n / (1 + count)) + 1 for name, count in df.items()}
        # Normalise around the mean so the multipliers centre on 1.0. Without
        # this the whole scale drifts with corpus size, and a score would move
        # week to week because more adverts were collected rather than because
        # anything about the candidate or the company changed.
        mean = sum(raw.values()) / len(raw)
        if mean > 0:
            for name, value in raw.items():
                weights[name] = max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, value / mean))

    log.info("rarity: %d terms weighted over %d adverts", len(weights), n)
    return Rarity(weights=weights, corpus_size=n, applies=True)


def none_for(signals: list[dict[str, Any]] | None = None) -> Rarity:
    """A Rarity that weights nothing, for callers that want the flat model."""
    return Rarity(weights={}, corpus_size=len(signals or []), applies=False)
