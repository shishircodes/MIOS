"""Which sources the next scrape collects from.

An administrator turns a source off from the Admin panel — because it is noisy,
because its credentials have lapsed, or because the site is down and its
failures are drowning the logs — and the next pipeline run skips it.

Two rules shape the storage:

**Only deviations are recorded.** A source with no row is enabled. So a scraper
added later is collected from by default, and adding one needs neither a
migration nor a seed. The table answers "what did somebody change?", not "what
sources exist?" — `scraper.SOURCE_NAMES` is the authority on that.

**Every change records who and when.** The question this table exists to answer
is "why did we collect nothing from SEEK last week?", and a bare boolean cannot
answer it.

This lives in `loader` rather than `api` because the pipeline reads it and the
API writes it. Putting it in `api` would make `pipeline` import `api`, which
already imports `pipeline` — a cycle.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loader.db import connect
from scraper import SOURCE_NAMES

log = logging.getLogger(__name__)


class UnknownSource(ValueError):
    """A name that is not a registered scraper. The message reaches the user."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _disabled(target: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Rows for sources that are switched off, keyed by name."""
    try:
        with connect(target, readonly=True) as conn:
            rows = conn.execute(
                "SELECT source_name, enabled, changed_by, changed_at, note "
                "FROM source_settings WHERE enabled = 0"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        # A missing table means nobody has ever changed anything, which is the
        # same observable state as every source being enabled. Failing open is
        # right here: the alternative is a database hiccup silently stopping
        # collection.
        log.warning("source_settings: could not read (%s) — treating all as enabled", exc)
        return {}
    return {str(r["source_name"]): dict(r) for r in rows}


def enabled_sources(target: str | Path | None = None) -> list[str]:
    """The sources the next scrape should use, in registry order.

    May be empty — that is a deliberate choice an administrator is allowed to
    make, and the caller must handle it rather than treating it as "all". See
    `pipeline.live`, which skips scraping entirely rather than passing an empty
    list to `scrape_all`; that function reads `[]` as falsy and would fall back
    to every source, doing the exact opposite of what was asked.
    """
    off = _disabled(target)
    return [name for name in SOURCE_NAMES if name not in off]


def list_settings(target: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Every registered source and whether it is on, for the Admin panel.

    Built from `SOURCE_NAMES` rather than from the table, so a source nobody has
    ever touched still appears — with `enabled: True`, which is what it is.
    """
    off = _disabled(target)
    out: dict[str, dict[str, Any]] = {}
    for name in SOURCE_NAMES:
        row = off.get(name)
        out[name] = {
            "enabled": row is None,
            "changedBy": (row or {}).get("changed_by"),
            "changedAt": (row or {}).get("changed_at"),
            "note": (row or {}).get("note"),
        }
    return out


def set_enabled(
    source_name: str,
    enabled: bool,
    *,
    changed_by: str,
    note: str | None = None,
    target: str | Path | None = None,
) -> dict[str, Any]:
    """Turn a source on or off. Returns the resulting state for that source."""
    if source_name not in SOURCE_NAMES:
        raise UnknownSource(
            f"'{source_name}' is not a registered source. "
            f"Known sources: {', '.join(SOURCE_NAMES)}."
        )

    with connect(target) as conn:
        if enabled:
            # Enabled is the absence of a row, so re-enabling deletes rather
            # than storing `enabled = 1`. Keeps "no row means on" true in one
            # direction only, which is the whole point of the design.
            conn.execute("DELETE FROM source_settings WHERE source_name = ?", (source_name,))
        else:
            conn.execute(
                "INSERT INTO source_settings "
                "(source_name, enabled, changed_by, changed_at, note) VALUES (?,?,?,?,?) "
                "ON CONFLICT (source_name) DO UPDATE SET "
                "enabled = 0, changed_by = ?, changed_at = ?, note = ?",
                (source_name, 0, changed_by, _now(), note, changed_by, _now(), note),
            )

    log.info("source_settings: %s %s %s", changed_by,
             "enabled" if enabled else "disabled", source_name)
    return list_settings(target)[source_name]
