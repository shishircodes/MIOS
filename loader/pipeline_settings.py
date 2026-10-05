"""How a pipeline run handles what it collects, as administrators set it.

These were constants in the code — fifty records per source, twenty-five per AI
call, twenty AI calls a day — so changing any of them meant a code change and a
redeploy. They now live in the `pipeline_settings` table, pre-filled with those
same values, and are edited from Admin › Data sources.

Three rules:

* **Every value has a range, and it is enforced here.** The ranges come from
  things that have gone wrong: a hundred records per AI call overran the
  model's output limit on 14 Sep 2026 and left 99 rows unclassified, so the
  batch size stops at fifty.
* **Reading never fails a run.** A missing table or row means the default, the
  value the code used before this existed. A database hiccup must not stop
  collection or classification.
* **The row records who changed it and when.** "Why did we only collect ten
  from Adzuna last week?" should have an answer on screen.

In `loader`, like `source_settings`, because the pipeline reads it and the API
writes it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Defaults — the values these settings were pre-filled with. The code used
# them as constants before, and still falls back to them.
# --------------------------------------------------------------------------

#: Records to take from each source per run. Per source rather than a total
#: budget, so one prolific board cannot crowd out a quiet one.
DEFAULT_SCRAPE_LIMIT = 50

#: Records per AI call. It was 100, sized so an 80-record run used one call.
#: The model's output limit, not its input, is what bounds a batch, and news
#: articles and tenders produce longer answers than job ads: once a run reached
#: 180 rows, a 100-row batch overran the limit, the truncated answer failed to
#: parse, and every row in it stayed unclassified (99 of 180 on 14 Sep 2026). A
#: failed batch also loses all its rows at once, so a small batch bounds the
#: damage too.
DEFAULT_BATCH_SIZE = 25

#: Characters of each record sent to the model. Long adverts are truncated; the
#: title, company and location come first, so little that matters is lost.
DEFAULT_MAX_CHARS = 3000

#: AI calls allowed per day across classification and report rewriting — the
#: Gemini free-tier limit. Raise it on a paid plan.
DEFAULT_DAILY_CALLS = 20

#: Seconds between AI calls, to stay under the per-minute burst limit.
DEFAULT_MIN_SECONDS = 13

_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_settings (
    key        TEXT PRIMARY KEY,
    value      INTEGER NOT NULL,
    changed_by TEXT,
    changed_at TEXT NOT NULL
)
"""

SEEDED_BY = "default"


@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    default: int
    low: int
    high: int
    unit: str
    help: str


CLASSIFIER_SPECS: tuple[Spec, ...] = (
    Spec("classify.batch_size", "Records per AI call", DEFAULT_BATCH_SIZE, 5, 50, "records",
         "How many records go to the model in one call."),
    Spec("classify.max_chars", "Characters kept per record", DEFAULT_MAX_CHARS, 500, 8000,
         "characters",
         "Longer records are cut to this length before they are sent to the model."),
    Spec("classify.daily_calls", "AI calls per day", DEFAULT_DAILY_CALLS, 1, 1000, "calls",
         "The most AI calls classification and report writing may make in a day."),
    Spec("classify.min_seconds", "Seconds between AI calls", DEFAULT_MIN_SECONDS, 0, 120,
         "seconds",
         "A pause between calls, to stay under the provider's per-minute limit."),
)

SCRAPE_LOW, SCRAPE_HIGH = 1, 500
SCRAPE_PREFIX = "scrape_limit:"


def _source_names() -> list[str]:
    from scraper import SOURCE_NAMES

    return list(SOURCE_NAMES)


def _specs() -> dict[str, Spec]:
    specs = {s.key: s for s in CLASSIFIER_SPECS}
    from scraper import catalog

    for name in _source_names():
        key = SCRAPE_PREFIX + name
        src = catalog.get(name)
        # The catalogue's own starting point for the source, and its name as a
        # reader knows it. A name the catalogue has lost falls back to the key.
        specs[key] = Spec(key, src.label if src else name,
                          src.limit if src else DEFAULT_SCRAPE_LIMIT,
                          SCRAPE_LOW, SCRAPE_HIGH, "records",
                          "Records taken from this source each run.")
    return specs


class SettingError(ValueError):
    """A value the panel may not set. The message reaches the administrator."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seed(conn) -> None:
    """Create the table and fill in any setting that has no row yet.

    Idempotent: an existing row — an administrator's choice — is never touched.
    Runs from `init_db`, and before the first read or write from the panel.
    """
    conn.execute(_DDL)
    stamp = _now()
    for spec in _specs().values():
        conn.execute(
            "INSERT INTO pipeline_settings (key, value, changed_by, changed_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (key) DO NOTHING",
            (spec.key, spec.default, SEEDED_BY, stamp),
        )


def _rows(target) -> dict[str, dict[str, Any]]:
    try:
        with connect(target, readonly=True) as conn:
            rows = conn.execute(
                "SELECT key, value, changed_by, changed_at FROM pipeline_settings").fetchall()
    except Exception as exc:  # noqa: BLE001 - no table yet: every value is its default
        log.debug("pipeline_settings: could not read (%s) — using defaults", exc)
        return {}
    return {str(r["key"]): dict(r) for r in rows}


def _value(spec: Spec, row: dict[str, Any] | None) -> int:
    """The stored value if it is usable, else the default. Out-of-range values
    (written by hand in the database) are clamped rather than trusted."""
    if row is None:
        return spec.default
    try:
        v = int(row["value"])
    except (TypeError, ValueError):
        return spec.default
    return min(max(v, spec.low), spec.high)


# --------------------------------------------------------------------------
# Reading — used by the pipeline and the classifier
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassifierSettings:
    batch_size: int
    max_chars: int
    daily_calls: int
    min_seconds: int


def classifier(target: str | Path | None = None) -> ClassifierSettings:
    rows = _rows(target)
    specs = {s.key: s for s in CLASSIFIER_SPECS}

    def v(key: str) -> int:
        return _value(specs[key], rows.get(key))

    return ClassifierSettings(
        batch_size=v("classify.batch_size"),
        max_chars=v("classify.max_chars"),
        daily_calls=v("classify.daily_calls"),
        min_seconds=v("classify.min_seconds"),
    )


def scrape_limits(target: str | Path | None = None) -> dict[str, int]:
    """Records to take from each registered source per run."""
    rows = _rows(target)
    specs = _specs()
    return {name: _value(specs[SCRAPE_PREFIX + name], rows.get(SCRAPE_PREFIX + name))
            for name in _source_names()}


# --------------------------------------------------------------------------
# The panel
# --------------------------------------------------------------------------


def describe(target: str | Path | None = None) -> dict[str, Any]:
    """Every setting with its value, range, default and who last changed it."""
    rows = _rows(target)

    def entry(spec: Spec) -> dict[str, Any]:
        row = rows.get(spec.key)
        changed = row and row.get("changed_by") not in (None, SEEDED_BY)
        return {
            "key": spec.key, "label": spec.label, "value": _value(spec, row),
            "default": spec.default, "min": spec.low, "max": spec.high,
            "unit": spec.unit, "help": spec.help,
            "changedBy": row["changed_by"] if changed else None,
            "changedAt": row["changed_at"] if changed else None,
        }

    specs = _specs()
    cls = [entry(s) for s in CLASSIFIER_SPECS]
    by_key = {c["key"]: c["value"] for c in cls}
    return {
        "sources": [entry(specs[SCRAPE_PREFIX + n]) | {"source": n} for n in _source_names()],
        "classifier": cls,
        #: What those numbers add up to, so the effect of a change is visible
        #: before the next run finds out.
        "derived": {
            "recordsPerDay": by_key["classify.batch_size"] * by_key["classify.daily_calls"],
            "secondsPerFullDay": by_key["classify.min_seconds"] * max(
                0, by_key["classify.daily_calls"] - 1),
        },
    }


def update(values: dict[str, Any], *, changed_by: str,
           target: str | Path | None = None) -> list[str]:
    """Validate every value first, then write them all. Returns the keys changed.

    All or nothing: a form with one bad field saves none of them, so the
    settings never end up half-applied.
    """
    specs = _specs()
    clean: dict[str, int] = {}
    for key, raw in values.items():
        spec = specs.get(key)
        if spec is None:
            raise SettingError(f"'{key}' is not a setting this panel controls.")
        if isinstance(raw, bool):
            raise SettingError(f"{spec.label} must be a whole number.")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise SettingError(f"{spec.label} must be a whole number.") from None
        if value != raw and not (isinstance(raw, str) and raw.strip() == str(value)):
            raise SettingError(f"{spec.label} must be a whole number.")
        if not spec.low <= value <= spec.high:
            raise SettingError(
                f"{spec.label} must be between {spec.low} and {spec.high} {spec.unit}.")
        clean[key] = value

    current = _rows(target)
    changed = [k for k, v in clean.items()
               if _value(specs[k], current.get(k)) != v or k not in current]
    if not changed:
        return []
    stamp = _now()
    with connect(target) as conn:
        seed(conn)
        for key in changed:
            conn.execute(
                "UPDATE pipeline_settings SET value = ?, changed_by = ?, changed_at = ? "
                "WHERE key = ?",
                (clean[key], changed_by, stamp, key),
            )
    log.info("pipeline_settings: %s changed %s", changed_by,
             ", ".join(f"{k}={clean[k]}" for k in changed))
    return changed


__all__ = [
    "CLASSIFIER_SPECS", "ClassifierSettings", "DEFAULT_BATCH_SIZE", "DEFAULT_DAILY_CALLS",
    "DEFAULT_MAX_CHARS", "DEFAULT_MIN_SECONDS", "DEFAULT_SCRAPE_LIMIT", "SettingError",
    "classifier", "describe", "scrape_limits", "seed", "update",
]
