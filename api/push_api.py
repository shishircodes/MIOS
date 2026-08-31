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
from push.rationale import annotate
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
