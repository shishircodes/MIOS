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
import re
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

#: The values the matcher compares against. A free-text "Australia" in the
#: region field used to be stored as typed and then matched nothing, silently
#: zeroing region fit for that candidate on every company.
SECTORS = ("mining", "oil_gas", "construction", "defence", "energy_transition", "other")
REGIONS = ("AU", "PNG")
_REGION_ALIASES = {"australia": "AU", "au": "AU", "aus": "AU",
                   "papua new guinea": "PNG", "png": "PNG"}

#: Longest value kept per text field. Generous for real input; a cap so a
#: pasted CV in the notes box does not become a row nobody can load.
MAX_LEN = {"full_name": 120, "email": 200, "phone": 40, "current_title": 120,
           "availability": 120, "notes": 4000}

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProfileError(ValueError):
    """Bad profile input. The message is shown to the user."""


class ProfileConflict(ProfileError):
    """The person is already saved. Carries the existing profile."""

    def __init__(self, message: str, existing: dict[str, Any]):
        super().__init__(message)
        self.existing = existing


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


def _clean_text(value: Any, column: str) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text[:MAX_LEN.get(column, 200)] or None


def _clean_email(value: Any) -> str | None:
    email = _clean_text(value, "email")
    if email is None:
        return None
    if not _EMAIL.match(email):
        raise ProfileError(f"“{email}” is not an email address.")
    # Lower-cased so the same person typed twice is recognised as one.
    return email.lower()


def _phone_digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _clean_phone(value: Any) -> str | None:
    phone = _clean_text(value, "phone")
    if phone is None:
        return None
    if not 6 <= len(_phone_digits(phone)) <= 15:
        raise ProfileError(f"“{phone}” does not look like a phone number.")
    return phone


def _clean_sector(value: Any) -> str | None:
    text = _clean_text(value, "sector")
    if text is None:
        return None
    key = "_".join(text.lower().replace("&", " ").split())
    if key not in SECTORS:
        raise ProfileError(f"Sector must be one of: {', '.join(SECTORS)}.")
    return key


def _clean_region(value: Any) -> str | None:
    text = _clean_text(value, "region")
    if text is None:
        return None
    region = _REGION_ALIASES.get(text.lower())
    if region is None:
        raise ProfileError("Region must be Australia (AU) or Papua New Guinea (PNG).")
    return region


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
        "updatedAt": row["updated_at"],
    }


_SELECT = (
    "SELECT profile_id, full_name, email, phone, current_title, sector, "
    "years_experience, region, skills, availability, intake_source, "
    "source_filename, notes, created_at, updated_at FROM candidate_profiles "
)

#: Every column a caller can write, in the order `_clean` returns them.
_COLUMNS = ("full_name", "email", "phone", "current_title", "sector",
            "years_experience", "region", "skills", "availability", "notes")


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise writable fields. Raises ProfileError on bad input."""
    name = _clean_text(fields.get("full_name"), "full_name")
    # The only hard requirement. Everything else can be filled in later, and a
    # half-known consultant is still worth matching — but a nameless row is not
    # something the BD team can act on or find again.
    if not name:
        raise ProfileError("A candidate name is required.")
    return {
        "full_name": name,
        "email": _clean_email(fields.get("email")),
        "phone": _clean_phone(fields.get("phone")),
        "current_title": _clean_text(fields.get("current_title"), "current_title"),
        "sector": _clean_sector(fields.get("sector")),
        "years_experience": _clean_years(fields.get("years_experience")),
        "region": _clean_region(fields.get("region")),
        "skills": json.dumps(_clean_skills(fields.get("skills"))),
        "availability": _clean_text(fields.get("availability"), "availability"),
        "notes": _clean_text(fields.get("notes"), "notes"),
    }


def find_duplicate(clean: dict[str, Any], *, excluding: str | None = None,
                   target: str | Path | None = None) -> dict[str, Any] | None:
    """A saved profile that is evidently the same person, or None.

    The same email is the same person. The same phone number is only when the
    name matches too — an office switchboard is shared by many people.
    """
    email = clean.get("email")
    digits = _phone_digits(clean.get("phone"))
    if not email and len(digits) < 8:
        return None
    with connect(target) as conn:
        rows = conn.execute(
            _SELECT + "WHERE lower(email) = ? OR phone IS NOT NULL",
            (email or "",),
        ).fetchall()
    for row in rows:
        if row["profile_id"] == excluding:
            continue
        if email and (row["email"] or "").lower() == email:
            return to_api(row)
        if (len(digits) >= 8 and _phone_digits(row["phone"]) == digits
                and (row["full_name"] or "").casefold() == clean["full_name"].casefold()):
            return to_api(row)
    return None


def _conflict(existing: dict[str, Any]) -> ProfileConflict:
    added = (existing.get("createdAt") or "")[:10]
    return ProfileConflict(
        f"{existing['fullName']} is already saved (added {added}), with the same "
        f"{'email' if existing.get('email') else 'phone number'}. Open them from Saved "
        f"profiles to update that record instead of creating a second one.",
        existing,
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

    clean = _clean(_normalise(payload))
    existing = find_duplicate(clean, target=target)
    if existing:
        raise _conflict(existing)

    profile_id = f"prof-{uuid.uuid4().hex[:12]}"
    name = clean["full_name"]
    row = (profile_id, *(clean[c] for c in _COLUMNS[:8]),
           clean["availability"], intake_source, source_filename, clean["notes"], _now())

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


def update_profile(profile_id: str, payload: dict[str, Any], *,
                   target: str | Path | None = None) -> dict[str, Any] | None:
    """Replace a saved profile's fields with a reviewed version. None if missing.

    Every writable field is replaced, as the form sends the whole profile back:
    a field cleared on screen is cleared here. How the record first arrived
    (`intake_source`, `source_filename`) is history and is kept.
    """
    if get_profile(profile_id, target=target) is None:
        return None
    clean = _clean(_normalise(payload))
    existing = find_duplicate(clean, excluding=profile_id, target=target)
    if existing:
        raise _conflict(existing)
    with connect(target) as conn:
        conn.execute(
            "UPDATE candidate_profiles SET full_name = ?, email = ?, phone = ?, "
            "current_title = ?, sector = ?, years_experience = ?, region = ?, skills = ?, "
            "availability = ?, notes = ?, updated_at = ? WHERE profile_id = ?",
            (*(clean[c] for c in _COLUMNS), _now(), profile_id),
        )
    log.info("push: updated profile %s", profile_id)
    return get_profile(profile_id, target=target)


def _search(q: str | None) -> tuple[str, tuple[Any, ...]]:
    """A WHERE clause matching name, title, email or skills, case-insensitively."""
    q = (q or "").strip().lower()
    if not q:
        return "", ()
    like = f"%{q}%"
    return ("WHERE lower(full_name) LIKE ? OR lower(coalesce(current_title, '')) LIKE ? "
            "OR lower(coalesce(email, '')) LIKE ? OR lower(coalesce(skills, '')) LIKE ? ",
            (like, like, like, like))


def list_profiles(limit: int = 50, target: str | Path | None = None, *,
                  offset: int = 0, q: str | None = None) -> list[dict[str, Any]]:
    """Newest first. Most recently edited counts as newest."""
    where, args = _search(q)
    with connect(target) as conn:
        rows = conn.execute(
            _SELECT + where
            + "ORDER BY coalesce(updated_at, created_at) DESC, profile_id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
    return [to_api(r) for r in rows]


def count_profiles(q: str | None = None, target: str | Path | None = None) -> int:
    where, args = _search(q)
    with connect(target) as conn:
        return int(conn.execute(
            "SELECT count(*) FROM candidate_profiles " + where, args).fetchone()[0] or 0)


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
        # What the team decided about companies for this person is kept — the
        # outcomes are how the weights will one day be calibrated, and with the
        # profile gone they no longer identify anyone. The free-text note is
        # removed: it is the one field that may name them.
        try:
            with connect(target) as conn:
                conn.execute("UPDATE match_outcomes SET note = NULL WHERE profile_id = ?",
                             (profile_id,))
        except Exception as exc:  # noqa: BLE001 - no outcomes table yet
            log.debug("push: no outcome notes to clear (%s)", exc)
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
    """Classified signals from the last `days`, as plain dicts for the matcher.

    `signal_category` and `source_type` were missing from this query, so the
    signal-quality contributor never had anything to judge and every news story
    was read as a job advert. `region` is the effective market, which is what
    region fit should compare against.
    """
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with connect(target) as conn:
        rows = conn.execute(
            "SELECT company_name, sector, geography, region, watchlist_tier, raw_content, "
            "captured_at, signal_category, source_type FROM signals "
            "WHERE classified_at IS NOT NULL AND captured_at >= ? "
            "ORDER BY captured_at DESC",
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]
