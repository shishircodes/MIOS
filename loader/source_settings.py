"""Which sources the next scrape collects from.

An administrator turns a source off from the Admin panel — because it is noisy,
because its credentials have lapsed, or because the site is down and its
failures are drowning the logs — and the next pipeline run skips it.

Two rules shape the storage:

**Only deviations are recorded.** A source with no row sits at its default, so a
scraper added later is collected from without a migration or a seed. The table
answers "what did somebody change?", not "what sources exist?" —
`scraper.SOURCE_NAMES` is the authority on that.

**A source may default to off, and must then say why.** Most default to on. SEEK
does not: it answers 403 to the deployed server at their edge, so leaving it on
meant every run spent time on a source that could not return anything while the
digest quietly under-collected. An administrator can still switch it on — the
block is a property of where MIOS runs, not a permanent fact — but they are
told what they are turning on first.

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


#: Sources that start switched off. Absent means "on", which is every source
#: that actually works from the deployed host.
DEFAULT_ENABLED: dict[str, bool] = {
    "seek": False,
}

#: Why a source ships switched off. Shown beside its toggle and returned when
#: somebody switches it on: a default nobody can explain is one the next person
#: quietly reverts, discovers nothing collected, and re-diagnoses from scratch.
OFF_BY_DEFAULT_REASON: dict[str, str] = {
    "seek": (
        "SEEK returns HTTP 403 to this server's IP address, at their edge, "
        "before the request reaches the site. Every request fails within "
        "milliseconds however it is disguised - plain requests, browser "
        "headers, and both Chrome and Firefox impersonation were all refused "
        "identically - so this is a block on where MIOS is hosted, not on how "
        "it asks. It is not a robots.txt matter: the category pages this "
        "scraper reads are permitted, and the paths robots.txt disallows are "
        "already refused by the scraper itself. Adzuna covers the same "
        "Australian market through a licensed API and is unaffected. Turning "
        "SEEK on only helps if MIOS has moved to a network SEEK does not "
        "block; otherwise it collects nothing and only makes each run slower."
    ),
}


def default_enabled(source_name: str) -> bool:
    """Whether a source collects when nobody has expressed a preference."""
    return DEFAULT_ENABLED.get(source_name, True)


class UnknownSource(ValueError):
    """A name that is not a registered scraper. The message reaches the user."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _choices(target: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Every stored preference, keyed by name.

    Reads both values, not only the zeroes. A source that defaults to off needs
    a way to record "an administrator turned this on", and the absence of a row
    cannot express that.
    """
    try:
        with connect(target, readonly=True) as conn:
            rows = conn.execute(
                "SELECT source_name, enabled, changed_by, changed_at, note "
                "FROM source_settings"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        # A missing table means nobody has ever changed anything, which is the
        # same observable state as every source being enabled. Failing open is
        # right here: the alternative is a database hiccup silently stopping
        # collection.
        log.warning("source_settings: could not read (%s) — falling back to defaults", exc)
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
    chosen = _choices(target)
    out: list[str] = []
    for name in SOURCE_NAMES:
        row = chosen.get(name)
        on = bool(row["enabled"]) if row is not None else default_enabled(name)
        if on:
            out.append(name)
    return out


def list_settings(target: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Every registered source and whether it is on, for the Admin panel.

    Built from `SOURCE_NAMES` rather than from the table, so a source nobody has
    ever touched still appears — with `enabled: True`, which is what it is.
    """
    chosen = _choices(target)
    out: dict[str, dict[str, Any]] = {}
    for name in SOURCE_NAMES:
        row = chosen.get(name)
        out[name] = {
            "enabled": bool(row["enabled"]) if row is not None else default_enabled(name),
            "changedBy": (row or {}).get("changed_by"),
            "changedAt": (row or {}).get("changed_at"),
            "note": (row or {}).get("note"),
            #: What it would be had nobody touched it, and why — so the panel can
            #: explain a source that ships off instead of merely showing it off
            #: and inviting the next person to flip it back.
            "defaultEnabled": default_enabled(name),
            "offReason": OFF_BY_DEFAULT_REASON.get(name),
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
        if enabled == default_enabled(source_name):
            # Back at the default, so the deviation is deleted rather than
            # stored. That keeps the table a record of what somebody changed,
            # which is what lets a default be revised later without having to
            # rewrite every row that agreed with the old one.
            conn.execute("DELETE FROM source_settings WHERE source_name = ?", (source_name,))
        else:
            stamp = _now()
            conn.execute(
                "INSERT INTO source_settings "
                "(source_name, enabled, changed_by, changed_at, note) VALUES (?,?,?,?,?) "
                "ON CONFLICT (source_name) DO UPDATE SET "
                "enabled = ?, changed_by = ?, changed_at = ?, note = ?",
                (source_name, 1 if enabled else 0, changed_by, stamp, note,
                 1 if enabled else 0, changed_by, stamp, note),
            )

    log.info("source_settings: %s %s %s", changed_by,
             "enabled" if enabled else "disabled", source_name)
    return list_settings(target)[source_name]
