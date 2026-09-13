"""Which model an administrator has chosen for each purpose.

Routing can come from three places, and they are consulted in this order:

1. **This table** — what an administrator picked in the Admin panel.
2. **`LLM_ROUTING`** — an environment variable, for pinning a deployment.
3. **The built-in default** — Gemini, which is what every purpose used before
   any of this existed.

Only deviations are stored, the same shape `source_settings` uses: a purpose
with no row sits at whatever the environment or the default says. That is what
lets the default be revised later without rewriting rows that merely agreed with
the old one, and it means an administrator "resetting" a purpose is a delete
rather than a second kind of state.

The environment variable is deliberately kept as the *middle* layer rather than
being replaced. A deployment that pins a model — because a provider is having a
bad week, or because a key was revoked — should not be silently overridden by a
choice somebody made in the panel three weeks ago... except that it is, and on
purpose: the panel is the more recent and more deliberate act. What the panel
must therefore do is *show* that an environment value exists and is being
overridden, which the API does, so nobody debugs a model choice for an hour.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)


class UnknownPurpose(ValueError):
    """Not a purpose the pipeline has. The message reaches the administrator."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stored_routing(target: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Every stored choice, keyed by purpose.

    Fails open to an empty mapping: an unreadable table should leave the
    pipeline on its defaults, not stop it choosing a model at all.
    """
    try:
        with connect(target, readonly=True) as conn:
            rows = conn.execute(
                "SELECT purpose, provider, model, changed_by, changed_at "
                "FROM llm_settings").fetchall()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.warning("llm_settings: could not read (%s) — using defaults", exc)
        return {}
    return {str(r["purpose"]): dict(r) for r in rows}


def set_route(
    purpose: str,
    provider: str,
    model: str,
    *,
    changed_by: str,
    target: str | Path | None = None,
) -> None:
    """Point a purpose at a provider and model."""
    from llm.purposes import PURPOSES

    if purpose not in PURPOSES:
        raise UnknownPurpose(
            f"'{purpose}' is not a purpose this pipeline has. "
            f"Known: {', '.join(sorted(PURPOSES))}."
        )

    stamp = _now()
    with connect(target) as conn:
        conn.execute(
            "INSERT INTO llm_settings (purpose, provider, model, changed_by, changed_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT (purpose) DO UPDATE SET "
            "provider = ?, model = ?, changed_by = ?, changed_at = ?",
            (purpose, provider, model, changed_by, stamp,
             provider, model, changed_by, stamp),
        )
    log.info("llm_settings: %s routed %s -> %s/%s", changed_by, purpose, provider, model)


def clear_route(purpose: str, *, target: str | Path | None = None) -> bool:
    """Return a purpose to the environment or built-in default."""
    with connect(target) as conn:
        removed = conn.execute(
            "DELETE FROM llm_settings WHERE purpose = ?", (purpose,)).rowcount
    return bool(removed)
