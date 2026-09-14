"""Optional features an administrator can switch off.

Starts with one: the written rationale Mode Push asks a model for. It never
touches the score or the order (see `push/rationale.py`), so switching it off
changes what a consultant reads and what the day's model allowance is spent on,
and nothing about who gets contacted. That is what makes it safe to leave to an
administrator rather than a deploy.

Stored the same way as `source_settings`: **only deviations**. A feature with no
row sits at its default, so a feature added later needs no migration or seed,
and returning one to its default is a delete. **Every change records who and
when**, because "why have the AI notes stopped?" is exactly the question a bare
boolean cannot answer.

Reads fail open to the default. An unreadable table must not quietly switch a
feature off, and must not stop Mode Push returning a ranking.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Feature:
    name: str
    default: bool
    label: str
    what: str


PUSH_RATIONALE = "push_rationale"

FEATURES: dict[str, Feature] = {
    PUSH_RATIONALE: Feature(
        name=PUSH_RATIONALE,
        default=True,
        label="Mode Push AI notes",
        what=("A written rationale, a fit verdict and a caveat for the top matches. "
              "It never changes a score or the order, so turning it off only removes "
              "the notes and saves one model call per search."),
    ),
}

#: Kept identical to the table in `loader/schema.sql`. Created here as well so a
#: database initialised before this feature existed can still record a change.
_DDL = (
    "CREATE TABLE IF NOT EXISTS feature_settings ("
    "name TEXT PRIMARY KEY, "
    "enabled INTEGER NOT NULL, "
    "changed_by TEXT, "
    "changed_at TEXT NOT NULL)"
)


class UnknownFeature(ValueError):
    """Not a feature this application has. The message reaches the administrator."""


def _feature(name: str) -> Feature:
    try:
        return FEATURES[name]
    except KeyError:
        raise UnknownFeature(
            f"'{name}' is not a feature that can be switched. "
            f"Known: {', '.join(sorted(FEATURES))}."
        ) from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(name: str, target: str | Path | None) -> dict[str, Any] | None:
    try:
        with connect(target, readonly=True) as conn:
            row = conn.execute(
                "SELECT enabled, changed_by, changed_at FROM feature_settings WHERE name = ?",
                (name,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.debug("feature_settings: could not read (%s) — using the default", exc)
        return None
    return dict(row) if row is not None else None


def describe(name: str, target: str | Path | None = None) -> dict[str, Any]:
    """The feature's state and where it came from, for the Admin panel."""
    feature = _feature(name)
    row = _row(name, target)
    return {
        "name": feature.name,
        "label": feature.label,
        "what": feature.what,
        "enabled": bool(row["enabled"]) if row is not None else feature.default,
        "default": feature.default,
        "changedBy": (row or {}).get("changed_by"),
        "changedAt": (row or {}).get("changed_at"),
    }


def is_enabled(name: str, target: str | Path | None = None) -> bool:
    return bool(describe(name, target)["enabled"])


def set_enabled(name: str, enabled: bool, *, changed_by: str,
                target: str | Path | None = None) -> dict[str, Any]:
    """Switch a feature on or off. Returns the resulting state."""
    feature = _feature(name)
    with connect(target) as conn:
        conn.execute(_DDL)
        if enabled == feature.default:
            conn.execute("DELETE FROM feature_settings WHERE name = ?", (name,))
        else:
            stamp = _now()
            conn.execute(
                "INSERT INTO feature_settings (name, enabled, changed_by, changed_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT (name) DO UPDATE SET enabled = ?, changed_by = ?, changed_at = ?",
                (name, 1 if enabled else 0, changed_by, stamp,
                 1 if enabled else 0, changed_by, stamp),
            )
    log.info("feature_settings: %s turned %s %s", changed_by, name, "on" if enabled else "off")
    return describe(name, target)
