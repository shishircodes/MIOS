"""Reading and writing candidate profiles, and fetching the signals to match on.

Mode Push needs two things from the database: somewhere to keep the profiles the
BD team submits, and the classified signals Mode Monitor has already gathered.
Both live here so `push.matcher` can stay pure — it takes plain dictionaries and
knows nothing about storage, which is what makes it exhaustively testable.

The uploaded CV is never stored. `push.cv_extract` reads it in memory, the parsed
draft goes back to the browser for a human to correct, and only the corrected
fields are saved. The document itself is discarded, so it never reaches disk or a
backup — see the comment on `candidate_profiles` in loader/schema.sql.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)

#: Fields a caller may set. Anything else in the payload is ignored rather than
#: rejected, so a browser sending back a whole parsed draft (which carries
#: `confidence`) does not need to strip it first.
WRITABLE = (
    "full_name", "email", "phone", "current_title", "sector",
    "years_experience", "region", "skills", "availability", "notes",
)

#: camelCase over the wire, snake_case in the database.
_API_TO_DB = {
    "fullName": "full_name",
    "currentTitle": "current_title",
    "yearsExperience": "years_experience",
    "sourceFilename": "source_filename",
    "intakeSource": "intake_source",
}

INTAKE_SOURCES = ("cv_upload", "manual_form")


class ProfileError(ValueError):
    """Bad profile input. The message is shown to the user."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalise(payload: dict[str, Any]) -> dict[str, Any]:
    """camelCase API keys -> database column names, keeping only writable ones."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        col = _API_TO_DB.get(key, key)
        if col in WRITABLE:
            out[col] = value
    return out


def _clean_years(value: Any) -> int | None:
    if value in (None, "", "null"):
        return None
    try:
        years = int(value)
    except (TypeError, ValueError):
        raise ProfileError("Years of experience must be a whole number.") from None
    if not 0 <= years <= 60:
        raise ProfileError("Years of experience must be between 0 and 60.")
    return years


def _clean_skills(value: Any) -> list[str]:
    """Accept either a list or the comma-separated string a text input produces."""
    if not value:
        return []
    items = value.split(",") if isinstance(value, str) else list(value)
    seen: list[str] = []
    for item in items:
        s = str(item).strip().lower()
        if s and s not in seen:
            seen.append(s)
    return seen[:40]


def to_api(row: Any) -> dict[str, Any]:
    """One stored profile in the shape the web app reads."""
    skills_raw = row["skills"]
    try:
        skills = json.loads(skills_raw) if skills_raw else []
    except (TypeError, ValueError):
        skills = []
    return {
        "id": row["profile_id"],
        "fullName": row["full_name"],
        "email": row["email"],
        "phone": row["phone"],
        "currentTitle": row["current_title"],
        "sector": row["sector"],
        "yearsExperience": row["years_experience"],
        "region": row["region"],
        "skills": skills,
        "availability": row["availability"],
        "intakeSource": row["intake_source"],
        "sourceFilename": row["source_filename"],
        "notes": row["notes"],
        "createdAt": row["created_at"],
    }


_SELECT = (
    "SELECT profile_id, full_name, email, phone, current_title, sector, "
    "years_experience, region, skills, availability, intake_source, "
    "source_filename, notes, created_at FROM candidate_profiles "
)


def create_profile(
    payload: dict[str, Any],
    *,
    intake_source: str = "manual_form",
    source_filename: str | None = None,
    target: str | Path | None = None,
) -> dict[str, Any]:
    """Save a profile the BD team has reviewed. Returns it in API shape."""
    if intake_source not in INTAKE_SOURCES:
        raise ProfileError(f"intake_source must be one of {', '.join(INTAKE_SOURCES)}.")

    fields = _normalise(payload)

    # The only hard requirement. Everything else can be filled in later, and a
    # half-known consultant is still worth matching — but a nameless row is not
    # something the BD team can act on or find again.
    name = str(fields.get("full_name") or "").strip()
    if not name:
        raise ProfileError("A candidate name is required.")

    profile_id = f"prof-{uuid.uuid4().hex[:12]}"
    row = (
        profile_id,
        name[:120],
        (fields.get("email") or None),
        (fields.get("phone") or None),
        (fields.get("current_title") or None),
        (fields.get("sector") or None),
        _clean_years(fields.get("years_experience")),
        (fields.get("region") or None),
        json.dumps(_clean_skills(fields.get("skills"))),
        (fields.get("availability") or None),
        intake_source,
        source_filename,
        (fields.get("notes") or None),
        _now(),
    )

    with connect(target) as conn:
        conn.execute(
            "INSERT INTO candidate_profiles (profile_id, full_name, email, phone, "
            "current_title, sector, years_experience, region, skills, availability, "
            "intake_source, source_filename, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            row,
        )
    log.info("push: stored profile %s (%s, via %s)", profile_id, name, intake_source)
    return get_profile(profile_id, target=target)  # type: ignore[return-value]


def list_profiles(limit: int = 50, target: str | Path | None = None) -> list[dict[str, Any]]:
    with connect(target) as conn:
        rows = conn.execute(
            _SELECT + "ORDER BY created_at DESC, profile_id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [to_api(r) for r in rows]


def get_profile(profile_id: str, target: str | Path | None = None) -> dict[str, Any] | None:
    with connect(target) as conn:
        row = conn.execute(_SELECT + "WHERE profile_id = ?", (profile_id,)).fetchone()
    return to_api(row) if row else None


def delete_profile(profile_id: str, target: str | Path | None = None) -> bool:
    """Remove a profile. These are real people — being able to erase one on
    request is a baseline obligation, not a feature."""
    with connect(target) as conn:
        cur = conn.execute(
            "DELETE FROM candidate_profiles WHERE profile_id = ?", (profile_id,)
        )
        removed = (cur.rowcount or 0) > 0
    if removed:
        log.info("push: deleted profile %s", profile_id)
    return removed


# --------------------------------------------------------------------------
# Signals to match against
# --------------------------------------------------------------------------

#: How far back the matcher looks. Wider than the dashboard's 7-day window on
#: purpose: a consultant rolling off in 30 days can be pitched against a company
#: that was hiring three weeks ago, whereas the digest is specifically about the
#: current week.
DEFAULT_MATCH_WINDOW_DAYS = 30


def signals_for_matching(
    days: int = DEFAULT_MATCH_WINDOW_DAYS,
    target: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Classified signals from the last `days`, as plain dicts for the matcher."""
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with connect(target) as conn:
        rows = conn.execute(
            "SELECT company_name, sector, geography, watchlist_tier, raw_content, "
            "captured_at FROM signals "
            "WHERE classified_at IS NOT NULL AND captured_at >= ? "
            "ORDER BY captured_at DESC",
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]
