"""HTTP surface for Mode Push — profile intake and profile-to-client matching.

The flow this supports, which is the one the BD team actually performs:

    1. POST /api/push/parse-cv   upload a .docx or .pdf, get a *draft* back
    2.   (the browser shows the draft, flagged by confidence, for correction)
    3. POST /api/push/profiles   save the corrected fields
    4. GET  /api/push/profiles/{id}/matches   ranked companies to approach

Step 2 is the point of the split. The parser is rule-based and will sometimes be
wrong, so nothing is saved straight from a CV: a human confirms it first. That
also means the uploaded document never needs to be retained — it is parsed in
memory during step 1 and discarded when the request ends.

Every endpoint requires a signed-in Easy Skill account. These are real people's
CVs, so there is no public read path.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile

from api.auth import require_user
from push.cv_extract import MAX_BYTES, CVExtractionError, extract_text
from push.matcher import match_profile
from push.rationale import ANNOTATE_TOP_N, annotate
from push.profile_parser import parse_profile
from push.store import (
    DEFAULT_MATCH_WINDOW_DAYS,
    ProfileError,
    create_profile,
    delete_profile,
    get_profile,
    list_profiles,
    signals_for_matching,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/push", tags=["push"])

MAX_RESULTS = 25


def _matches_for(profile: dict[str, Any], *, days: int, limit: int,
                 explain: bool = True) -> dict[str, Any]:
    signals = signals_for_matching(days=days)
    results = match_profile(profile, signals, limit=limit)
    matches = [m.to_dict(rank=i + 1) for i, m in enumerate(results)]

    # The written half. Deliberately after the ranking is fixed and unable to
    # change it — see push/rationale.py. Every failure here returns the ranking
    # unannotated, so a spent quota costs the prose and not the result.
    note = None
    if explain:
        matches, note = annotate(profile, matches)

    return {
        "profile": profile,
        "matches": matches,
        #: Why there is no written rationale, when there is none. Absent when
        #: the annotation worked.
        "rationaleNote": note,
        "windowDays": days,
        #: How much evidence the ranking is standing on. A short list of matches
        #: means something different when it came from 12 signals than from 900,
        #: and the UI says which.
        "signalsConsidered": len(signals),
    }


@router.post("/parse-cv")
async def parse_cv(
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """Read a CV and return a draft profile. Nothing is saved.

    The draft carries a per-field confidence so the UI can highlight what needs
    checking. A parse failure is a 400 with a message written for the person who
    uploaded the file, not a stack trace.
    """
    data = await file.read()
    filename = file.filename or "cv"
    try:
        text = extract_text(data, filename)
        draft = parse_profile(text).to_dict()
    except CVExtractionError as exc:
        # Expected and explained — bad file type, a scan, an empty document.
        log.info("push: CV rejected for %s (%s): %s", user["email"], filename, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log.info("push: parsed CV %s for %s (%d chars)", filename, user["email"], len(text))
    return {
        "draft": draft,
        "sourceFilename": filename,
        "charactersRead": len(text),
        #: Reminds the UI (and anyone reading the payload) that this is not a
        #: saved record — the caller must POST /profiles to keep it.
        "saved": False,
    }


@router.get("/profiles")
def profiles(
    limit: int = Query(50, ge=1, le=200),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    return {"profiles": list_profiles(limit=limit)}


@router.post("/profiles", status_code=201)
def save_profile(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """Store a profile the BD team has reviewed.

    `intakeSource` records how the data arrived — a CV that a human corrected is
    still 'cv_upload'. It describes provenance, not how much to trust the row.
    """
    intake = payload.get("intakeSource") or "manual_form"
    try:
        profile = create_profile(
            payload,
            intake_source=intake,
            source_filename=payload.get("sourceFilename"),
        )
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("push: %s saved profile %s", user["email"], profile["id"])
    return profile


@router.get("/profiles/{profile_id}")
def one_profile(
    profile_id: str,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No such profile.")
    return profile


@router.delete("/profiles/{profile_id}")
def remove_profile(
    profile_id: str,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    if not delete_profile(profile_id):
        raise HTTPException(status_code=404, detail="No such profile.")
    log.info("push: %s deleted profile %s", user["email"], profile_id)
    return {"deleted": profile_id}


@router.get("/profiles/{profile_id}/matches")
def profile_matches(
    profile_id: str,
    days: int = Query(DEFAULT_MATCH_WINDOW_DAYS, ge=1, le=365),
    limit: int = Query(10, ge=1, le=MAX_RESULTS),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """Companies to approach about this candidate, strongest first."""
    profile = get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No such profile.")
    log.info("push: matching %s for %s (%dd window)", profile_id, user["email"], days)
    return _matches_for(profile, days=days, limit=limit)


@router.post("/match")
def match_unsaved(
    payload: dict[str, Any] = Body(...),
    days: int = Query(DEFAULT_MATCH_WINDOW_DAYS, ge=1, le=365),
    limit: int = Query(10, ge=1, le=MAX_RESULTS),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """Rank companies for a profile without saving it.

    Lets a consultant try a CV against the market before deciding whether the
    person belongs in the database — which keeps speculative searches from
    accumulating personal data nobody chose to keep.
    """
    log.info("push: ad-hoc match for %s (%dd window)", user["email"], days)
    return _matches_for(payload, days=days, limit=limit)


#: Re-exported so the web app can enforce the same limit before uploading and
#: give an instant error instead of a round trip.
UPLOAD_LIMIT_BYTES = MAX_BYTES


@router.get("/scoring")
def scoring_model(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """How a match score is arrived at.

    Served from the scorer's own constants rather than written out in the
    interface. A description of the model kept separately from the model drifts
    the first time a weight is tuned, and it drifts silently — the screen would
    keep explaining a calculation that no longer happens.
    """
    from llm import PURPOSE_PUSH, available_providers, resolve
    from push import matcher

    provider, model = resolve(PURPOSE_PUSH)
    # The human label, not the internal key: "gemini · gemini-2.5-flash" reads
    # like a stutter on screen.
    label = next((p["label"] for p in available_providers() if p["name"] == provider), provider)
    return {
        "total": (matcher.W_ROLE + matcher.W_SKILLS + matcher.W_SIGNAL_QUALITY
                  + matcher.W_SECTOR + matcher.W_MOMENTUM + matcher.W_VOLUME
                  + matcher.W_RELATIONSHIP + matcher.W_SENIORITY + matcher.W_REGION
                  + matcher.W_RECENCY),
        "contributors": [
            {"key": "role", "weight": matcher.W_ROLE, "label": "Role demand",
             "what": "How closely the roles they are advertising match the candidate's job "
                     "title. Compared loosely, so “Snr Maint. Planner” and “Senior "
                     "Maintenance Planner” count as the same discipline."},
            {"key": "skills", "weight": matcher.W_SKILLS, "label": "Skills overlap",
             "what": "How many of the candidate's skills actually appear in the adverts. "
                     "Matched as whole words: a skill is something an employer either asked "
                     "for or did not."},
            {"key": "signalQuality", "weight": matcher.W_SIGNAL_QUALITY,
             "label": "Signal quality",
             "what": "What kind of signals these are, not just how many. A new project or a "
                     "leadership change is a decision point; routine vacancies mean the "
                     "company is ticking over."},
            {"key": "sector", "weight": matcher.W_SECTOR, "label": "Sector fit",
             "what": "How much of their hiring is in the candidate's sector."},
            {"key": "momentum", "weight": matcher.W_MOMENTUM, "label": "Momentum",
             "what": "Whether their hiring is accelerating against their own recent average — "
                     "the difference between a good account and a good week to call one."},
            {"key": "volume", "weight": matcher.W_VOLUME, "label": "Hiring volume",
             "what": "How much they are hiring right now, levelling off past a handful of "
                     "roles."},
            {"key": "relationship", "weight": matcher.W_RELATIONSHIP, "label": "Relationship",
             "what": "Whether they are already a watchlist client. A new name still scores — "
                     "it is a genuine opportunity, just a colder one."},
            {"key": "seniority", "weight": matcher.W_SENIORITY, "label": "Seniority fit",
             "what": "Whether the level being advertised matches the candidate's experience. "
                     "Silent when either is unknown rather than assuming a fit."},
            {"key": "region", "weight": matcher.W_REGION, "label": "Region fit",
             "what": "Whether they are hiring in the candidate's market."},
            {"key": "recency", "weight": matcher.W_RECENCY, "label": "Recency",
             "what": "How fresh the signals are, fading to nothing over a month."},
        ],
        "normalisation": (
            "A contributor that has nothing to judge — no skills recorded on the "
            "profile, no seniority stated in the adverts, no earlier week to measure "
            "a trend against — is left out of the total rather than scored zero. "
            "Charging a company for a gap in our own data would make it look worse "
            "than the evidence says. The points earned are then scaled to 100, so a "
            "company judged on 86 points that earns 60 of them shows as 70. Each row "
            "says what it was assessed on when that is not the whole model."
        ),
        "confidence": [
            {"level": "high", "what": "Eight or more signals across more than one collection."},
            {"level": "medium", "what": "At least three signals."},
            {"level": "low", "what": "One or two signals — a lead, not a finding. Also "
                                     "used whenever less than 60 of the 100 points could "
                                     "be judged, however many signals there are: a score "
                                     "scaled up from a narrow assessment is arithmetically "
                                     "right and a poor thing to act on."},
        ],
        "llm": {
            "provider": label,
            "model": model,
            "annotatesTop": ANNOTATE_TOP_N,
            "what": "A model writes the rationale and gives its own read of the fit. It "
                    "cannot change the score or the order — the ranking has to stay "
                    "reproducible, so where the model disagrees it is shown as a flag "
                    "for you to look at rather than applied to the number.",
        },
        "caveat": "The weights are judgement, not calibration: nobody has been placed "
                  "through this yet. Every score is shown broken down so the judgement can "
                  "be argued with, and the weights are expected to change once the team can "
                  "say what actually predicts a placement.",
    }
