"""Who may use MIOS, and what they may reach.

Two roles:

* **admin**  — everything, plus the Admin section: source health, usage and
  cost, and the access list itself.
* **member** — the intelligence pages. No Admin section.

There are three ways in, and they are deliberately different in kind:

1. **The Workspace domain** (`ALLOWED_GOOGLE_DOMAIN`). Everyone at Easy Skill,
   admitted as a `member`.
2. **This table.** A named person, at any address, with an explicit role. This
   is how someone outside the Workspace gets in without widening the domain rule
   for everyone else, and it is the only route that can grant `admin`.
3. **`ALLOWED_EMAILS`** in the environment. Still honoured, because pulling it
   would lock people out on the next deploy. But it is invisible from inside the
   app unless something surfaces it, so `env_grants()` exists to list it on the
   admin screen — an access route nobody can see is one nobody revokes.

`BOOTSTRAP_ADMIN` is seeded on first run. Without it the table starts empty,
nobody is an admin, and there is no way to make one from inside the app.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from config.settings import settings
from loader.db import connect

log = logging.getLogger(__name__)

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLES = (ROLE_ADMIN, ROLE_MEMBER)

#: Seeded on first run so the access list is never a locked room with the key
#: inside. Changing this later does nothing — the row already exists, and
#: promoting or removing it is done through the admin screen.
BOOTSTRAP_ADMIN = "revgames7@gmail.com"


class AccessError(ValueError):
    """A rejected change to the access list. The message is shown to the user."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalise(email: str) -> str:
    return (email or "").strip().lower()


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _row_to_api(row: Any) -> dict[str, Any]:
    return {
        "email": row["email"],
        "role": row["role"],
        "addedBy": row["added_by"],
        "addedAt": row["added_at"],
        "note": row["note"],
        "lastSeen": row["last_seen"],
        #: Rows come from the database and can be changed here. Environment
        #: grants cannot, and the UI needs to tell them apart.
        "source": "database",
    }


def get_user(email: str, target: str | Path | None = None) -> dict[str, Any] | None:
    email = normalise(email)
    if not email:
        return None
    try:
        with connect(target) as conn:
            row = conn.execute(
                "SELECT email, role, added_by, added_at, note, last_seen "
                "FROM app_users WHERE email = ?", (email,)
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.warning("access: could not read app_users (%s)", exc)
        return None
    return _row_to_api(row) if row else None


def list_users(target: str | Path | None = None) -> list[dict[str, Any]]:
    """Admins first, then members, alphabetically within each."""
    try:
        with connect(target) as conn:
            rows = conn.execute(
                "SELECT email, role, added_by, added_at, note, last_seen "
                "FROM app_users ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, email"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("access: could not list app_users (%s)", exc)
        return []
    return [_row_to_api(r) for r in rows]


def env_grants() -> list[dict[str, Any]]:
    """What `ALLOWED_EMAILS` currently admits.

    Surfaced so an admin can see every door, not just the ones this screen can
    close. These entries cannot be edited or revoked from the app — that takes
    a change to the environment and a restart — so they are returned marked as
    such rather than mixed in with the editable rows.
    """
    return [{
        "email": normalise(e),
        "role": ROLE_MEMBER,
        "addedBy": None,
        "addedAt": None,
        "note": "Granted by the ALLOWED_EMAILS environment variable",
        "lastSeen": None,
        "source": "environment",
    } for e in settings.allowed_emails if normalise(e)]


def count_external_grants(domain: str, target: str | Path | None = None) -> int:
    """Named accounts sitting outside the Workspace domain.

    A count rather than a listing: this runs on the sign-in redirect, and the
    only thing either caller needs is whether the number is zero.
    """
    domain = (domain or "").strip().lower()
    if not domain:
        return 0
    try:
        with connect(target) as conn:
            row = conn.execute(
                "SELECT count(*) FROM app_users WHERE email NOT LIKE ?",
                ("%@" + domain,),
            ).fetchone()
        return int(row[0])
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.warning("access: could not count external grants (%s)", exc)
        return 0


def has_external_grants(
    *,
    domain: str,
    allowed_emails: Iterable[str],
    target: str | Path | None = None,
) -> bool:
    """True when somebody outside the Workspace domain can sign in.

    Two callers need this and both used to ask `bool(settings.allowed_emails)`,
    which was correct only while the environment was the sole way in for a
    non-domain account. The `app_users` table is now another, so leaving
    ALLOWED_EMAILS empty no longer means "domain accounts only".

    The policy is passed in rather than read from `settings` here: the callers
    live in `api.auth` and hold their own reference to it, and a function that
    silently consulted a *different* settings object than its caller would be
    both untestable and wrong the moment the two diverged.

    A database that cannot be read reports no exceptions. That is the safe
    direction — both callers use this for presentation, and a table-granted
    sign-in would be failing anyway if this query is failing.
    """
    if any(allowed_emails):
        return True
    return count_external_grants(domain, target) > 0


def role_for(email: str, hd: str | None = None,
             target: str | Path | None = None) -> str | None:
    """The caller's role, or None if they are not admitted at all.

    Checked in the same order the sign-in policy applies: an explicit row wins,
    then the environment allowlist, then the Workspace domain.
    """
    email = normalise(email)
    row = get_user(email, target)
    if row:
        return row["role"]
    if email in {normalise(e) for e in settings.allowed_emails}:
        return ROLE_MEMBER
    domain = settings.allowed_google_domain.strip().lower()
    if domain and (hd or "").strip().lower() == domain:
        return ROLE_MEMBER
    if not domain and not settings.allowed_emails:
        # Nothing is configured. authorize_claims already warns loudly about
        # this; matching its behaviour here keeps one policy, not two.
        return ROLE_MEMBER
    return None


def is_admin(email: str, target: str | Path | None = None) -> bool:
    row = get_user(email, target)
    return bool(row and row["role"] == ROLE_ADMIN)


def count_admins(target: str | Path | None = None) -> int:
    try:
        with connect(target) as conn:
            row = conn.execute(
                "SELECT count(*) FROM app_users WHERE role = ?", (ROLE_ADMIN,)
            ).fetchone()
        return int(row[0])
    except Exception:  # noqa: BLE001
        return 0


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def ensure_bootstrap_admin(target: str | Path | None = None) -> None:
    """Seed the first admin if the table has none. Idempotent.

    Only fires when there is not a single admin, so it cannot resurrect an
    account somebody deliberately demoted or removed — unless they removed the
    last one, which is the case this exists for.
    """
    try:
        if count_admins(target) > 0:
            return
        with connect(target) as conn:
            conn.execute(
                "INSERT INTO app_users (email, role, added_by, added_at, note) "
                "VALUES (?,?,?,?,?) ON CONFLICT (email) DO UPDATE SET role = ?",
                (BOOTSTRAP_ADMIN, ROLE_ADMIN, "system", _now(),
                 "Seeded automatically as the first administrator", ROLE_ADMIN),
            )
        log.info("access: seeded %s as the bootstrap administrator", BOOTSTRAP_ADMIN)
    except Exception as exc:  # noqa: BLE001 - never block startup on this
        log.warning("access: could not seed the bootstrap admin (%s)", exc)


def upsert_user(
    email: str,
    role: str,
    *,
    added_by: str,
    note: str | None = None,
    target: str | Path | None = None,
) -> dict[str, Any]:
    """Grant access, or change an existing role."""
    email = normalise(email)
    if not email or "@" not in email:
        raise AccessError("That does not look like an email address.")
    if role not in ROLES:
        raise AccessError(f"Role must be one of: {', '.join(ROLES)}.")

    existing = get_user(email, target)
    # Demoting the last admin would leave the access list unmanageable, with no
    # way back in short of editing the database by hand.
    if (existing and existing["role"] == ROLE_ADMIN and role != ROLE_ADMIN
            and count_admins(target) <= 1):
        raise AccessError(
            "That is the only administrator. Promote someone else before "
            "changing this role."
        )

    with connect(target) as conn:
        conn.execute(
            "INSERT INTO app_users (email, role, added_by, added_at, note) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT (email) DO UPDATE SET role = ?, note = ?",
            (email, role, added_by, _now(), note, role, note),
        )
    log.info("access: %s set %s to %s", added_by, email, role)
    return get_user(email, target)  # type: ignore[return-value]


def remove_user(email: str, *, removed_by: str,
                target: str | Path | None = None) -> bool:
    email = normalise(email)
    existing = get_user(email, target)
    if not existing:
        return False
    if existing["role"] == ROLE_ADMIN and count_admins(target) <= 1:
        raise AccessError(
            "That is the only administrator. Promote someone else before "
            "removing this account."
        )
    with connect(target) as conn:
        conn.execute("DELETE FROM app_users WHERE email = ?", (email,))
    log.info("access: %s removed %s", removed_by, email)
    return True


def touch_last_seen(email: str, target: str | Path | None = None) -> None:
    """Record a sign-in, so an admin can see which grants are actually used."""
    email = normalise(email)
    if not email:
        return
    try:
        with connect(target) as conn:
            conn.execute(
                "UPDATE app_users SET last_seen = ? WHERE email = ?", (_now(), email)
            )
    except Exception as exc:  # noqa: BLE001 - never fail a login over telemetry
        log.debug("access: could not record last_seen for %s (%s)", email, exc)
