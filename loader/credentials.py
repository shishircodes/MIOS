"""Provider API keys an administrator entered in the panel.

Keys used to be environment-only, so changing one meant editing a secret in
GitHub and waiting for a redeploy. That is the wrong shape for the thing it
guards: a key gets rotated because it leaked, or because the free tier ran out
and somebody has a fresh one — both moments where "redeploy first" is the
opposite of what is wanted.

Three decisions worth stating, because each has a plausible-looking alternative:

**Stored keys win over the environment, and the environment is not removed.**
The same order `llm_settings` uses. A row here is the more recent and more
deliberate act, so it takes precedence — but `GEMINI_API_KEY` still works
untouched on a deployment nobody has opened the panel on, and the API reports
when a stored key is shadowing an environment one so that is visible rather
than mysterious.

**The key is encrypted with a value that is not in the database.**
`MIOS_CREDENTIAL_KEY` stays in the environment. Without it a database dump, a
leaked read-only `DATABASE_URL`, or a Neon console session hands over every
provider key in usable form — and these are bearer tokens that spend money, so
that is a materially different loss from the rest of the rows in there.

**Missing bootstrap key disables entry, but not reading.**
If `MIOS_CREDENTIAL_KEY` is unset, `set_key` refuses and the panel says why.
It does not quietly store plaintext, and it does not stop the app: the
environment keys still resolve, so a deployment without the variable behaves
exactly as it did before this module existed.

Note on what is *not* used for the encryption: `settings.session_secret` looks
like the obvious candidate and is a trap. It falls back to
`secrets.token_urlsafe(48)` minted per process when `SESSION_SECRET` is unset,
so every restart would produce a different derivation and turn all stored keys
into undecryptable noise — discovered as "the key I saved yesterday stopped
working", which is a bad way to find out.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)

#: The bootstrap secret. Named for the app rather than a provider: it protects
#: every stored key, not Gemini's.
KEY_ENV = "MIOS_CREDENTIAL_KEY"

#: Fixed, and not a secret. A salt's job here is to make the derivation specific
#: to this application so the same passphrase used elsewhere does not produce
#: the same encryption key; it does not need to be hidden, and a random one
#: would have to be stored somewhere — which puts us back where we started.
_SALT = b"mios.llm_credentials.v1"

#: PBKDF2 rather than a bare hash, because the environment value is chosen by a
#: person and may well be a passphrase rather than 32 random bytes. Derived once
#: per process; the cost never lands on a request.
_ITERATIONS = 200_000

#: How much of the key is kept in clear so an administrator can tell which one
#: is loaded. Four characters identifies a key to somebody holding it and is
#: useless to somebody who is not.
HINT_CHARS = 4


class CredentialError(RuntimeError):
    """Something went wrong storing or reading a key."""


class CredentialsLocked(CredentialError):
    """No bootstrap secret, so keys cannot be encrypted. Entry is refused."""


class CredentialUnreadable(CredentialError):
    """A stored key could not be decrypted with the current bootstrap secret."""


#: Created on demand as well as by `schema.sql`, the same way `llm.usage` treats
#: `kv_store`. The schema is only applied by `init_db`, which runs during a
#: pipeline run — so on a deployment that has not run since this shipped, the
#: table would not exist until the next Monday and the panel would refuse a key
#: for a reason no administrator could act on.
#:
#: Written in the dialect-neutral subset both SQLite and Postgres accept, and
#: kept identical to the definition in `schema.sql`; a test compares the columns
#: the two produce so they cannot drift.
_DDL = """
CREATE TABLE IF NOT EXISTS llm_credentials (
    provider   TEXT PRIMARY KEY,
    secret     TEXT NOT NULL,
    hint       TEXT,
    changed_by TEXT,
    changed_at TEXT NOT NULL
)
"""


def _ensure_table(conn) -> None:
    conn.execute(_DDL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bootstrap_secret() -> str:
    """The configured bootstrap value, or "".

    Read from the environment on each call rather than captured at import, so a
    test can set it and so a process that gains it does not need restarting to
    notice.
    """
    return os.environ.get(KEY_ENV, "").strip()


def available() -> bool:
    """Whether keys can be stored at all. The panel asks before offering a form."""
    return bool(bootstrap_secret())


@lru_cache(maxsize=4)
def _fernet_for(secret: str):
    """The cipher for this bootstrap secret.

    Cached on the secret itself, so rotating `MIOS_CREDENTIAL_KEY` produces a
    different cipher rather than a stale one, and the 200k-round derivation
    happens once per distinct value instead of once per request.
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise CredentialsLocked(
            "The `cryptography` package is not installed, so API keys cannot be "
            "stored in the panel. Keys set in the environment still work."
        ) from exc

    material = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), _SALT, _ITERATIONS)
    return Fernet(base64.urlsafe_b64encode(material))


def _cipher():
    secret = bootstrap_secret()
    if not secret:
        raise CredentialsLocked(
            f"{KEY_ENV} is not set, so there is nothing to encrypt an API key with. "
            f"Set it in the deployment environment and restart, then keys can be "
            f"entered here. Keys set as environment variables still work without it."
        )
    return _fernet_for(secret)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def stored_rows(target: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Every stored credential row, keyed by provider. Secrets still encrypted.

    Fails open to an empty mapping: an unreadable table should leave the app on
    its environment keys, not stop it reaching a model at all. The table may
    also simply not exist yet, on a database created before this feature.
    """
    try:
        with connect(target, readonly=True) as conn:
            rows = conn.execute(
                "SELECT provider, secret, hint, changed_by, changed_at "
                "FROM llm_credentials").fetchall()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.warning("credentials: could not read (%s) — using environment keys", exc)
        return {}
    return {str(r["provider"]): dict(r) for r in rows}


def stored_key(provider: str, target: str | Path | None = None,
               rows: dict[str, dict[str, Any]] | None = None) -> str | None:
    """The decrypted key for this provider, or None.

    None covers every reason there isn't one — no row, no bootstrap secret, or
    a row that will not decrypt — because the caller's next move is the same in
    all three: use the environment key. The *reason* is surfaced separately by
    `describe`, which is what the panel reads, so a key encrypted under a
    rotated secret is reported there rather than silently behaving as absent.

    `rows` lets a caller checking several providers read the table once.
    """
    row = (stored_rows(target) if rows is None else rows).get(provider)
    if not row or not row.get("secret"):
        return None
    if not bootstrap_secret():
        return None
    try:
        return _cipher().decrypt(str(row["secret"]).encode("utf-8")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - a bad token is not a crash
        log.warning("credentials: stored %s key will not decrypt (%s)", provider, exc)
        return None


def key_for(provider: str, env_value: str = "",
            target: str | Path | None = None,
            rows: dict[str, dict[str, Any]] | None = None) -> str:
    """The key this provider should use: the stored one, else the environment.

    The single place that order is decided. Providers call this instead of
    reading `settings` directly, so "which key is actually in play" has one
    answer rather than one per provider.
    """
    return stored_key(provider, target, rows) or env_value


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def set_key(provider: str, api_key: str, *, changed_by: str,
            target: str | Path | None = None) -> str:
    """Store a key for this provider. Returns the hint, never the key.

    Raises `CredentialsLocked` when there is no bootstrap secret — deliberately
    louder than storing plaintext, which would leave an administrator believing
    the key was protected.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        raise CredentialError("An API key is required.")

    cipher = _cipher()  # before the write, so a locked deployment stores nothing
    token = cipher.encrypt(api_key.encode("utf-8")).decode("utf-8")
    hint = api_key[-HINT_CHARS:]
    stamp = _now()

    with connect(target) as conn:
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO llm_credentials (provider, secret, hint, changed_by, changed_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT (provider) DO UPDATE SET "
            "secret = ?, hint = ?, changed_by = ?, changed_at = ?",
            (provider, token, hint, changed_by, stamp,
             token, hint, changed_by, stamp),
        )
    # The key itself is never logged, here or anywhere. The hint is, because
    # "which key was installed when this started failing" is a real question.
    log.info("credentials: %s set the %s key (…%s)", changed_by, provider, hint)
    return hint


def clear_key(provider: str, target: str | Path | None = None) -> bool:
    """Forget the stored key, returning this provider to its environment variable."""
    with connect(target) as conn:
        removed = conn.execute(
            "DELETE FROM llm_credentials WHERE provider = ?", (provider,)).rowcount
    if removed:
        log.info("credentials: %s key cleared — falling back to the environment", provider)
    return bool(removed)


# --------------------------------------------------------------------------
# Describing, for the panel
# --------------------------------------------------------------------------


def describe(provider: str, env_value: str = "",
             rows: dict[str, dict[str, Any]] | None = None,
             target: str | Path | None = None) -> dict[str, Any]:
    """Where this provider's key comes from, without disclosing it.

    `source` is the point of this. An administrator looking at a failing
    provider needs to know whether the key in play is the one they just typed,
    one pinned in the deployment, or none — which is three different next
    actions and, without this, an hour of guessing.
    """
    rows = stored_rows(target) if rows is None else rows
    row = rows.get(provider)
    unlocked = bool(bootstrap_secret())

    stored: str | None = None
    unreadable = False
    if row and row.get("secret"):
        if not unlocked:
            unreadable = True
        else:
            stored = stored_key(provider, target, rows)
            unreadable = stored is None

    if stored:
        source = "panel"
        hint = row.get("hint") if row else None
    elif env_value:
        source = "environment"
        hint = env_value[-HINT_CHARS:]
    else:
        source = "none"
        hint = None

    return {
        "provider": provider,
        "source": source,
        "hint": hint,
        #: True when a stored key exists and the environment also has one, so
        #: the panel can say which is winning rather than leaving somebody to
        #: wonder why their environment variable appears to do nothing.
        "shadowsEnvironment": bool(stored and env_value),
        #: A stored row that will not decrypt: almost always MIOS_CREDENTIAL_KEY
        #: changed. Worth naming, because the symptom otherwise looks identical
        #: to no key at all and the fix is completely different.
        "unreadable": unreadable,
        "canStore": unlocked,
        "changedBy": (row or {}).get("changed_by"),
        "changedAt": (row or {}).get("changed_at"),
    }
