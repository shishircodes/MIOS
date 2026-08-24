"""Ingestion. Loads schema, watchlist, and raw scraped signals.

Backend-agnostic: writes to Neon PostgreSQL when `DATABASE_URL` is set, SQLite
otherwise. See `loader/db.py`.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from config.settings import settings
from loader.db import SCHEMA_PATH, connect, describe

log = logging.getLogger(__name__)


#: Columns added to tables that already shipped. `CREATE TABLE IF NOT EXISTS`
#: does nothing at all when the table exists, so a column added to schema.sql
#: after the first deploy never reaches an existing database — it fails at
#: query time with "column does not exist", which is how this was found.
#:
#: Postgres has `ADD COLUMN IF NOT EXISTS`; SQLite does not, so the columns are
#: checked before being added rather than relying on either dialect.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("reports", "prose_source", "TEXT NOT NULL DEFAULT 'computed'"),
    ("reports", "prose_note", "TEXT"),
    ("report_sections", "computed_body", "TEXT"),
)


def _existing_columns(conn, table: str) -> set[str]:
    if conn.is_postgres:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
        return {str(r[0]) for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows}


def _apply_column_additions(conn) -> None:
    """Add any column in ADDED_COLUMNS that the database is missing. Idempotent."""
    by_table: dict[str, set[str]] = {}
    for table, column, decl in ADDED_COLUMNS:
        if table not in by_table:
            try:
                by_table[table] = _existing_columns(conn, table)
            except Exception as exc:  # noqa: BLE001 - table may not exist yet
                log.debug("init_db: cannot inspect %s (%s)", table, exc)
                by_table[table] = set()
        if not by_table[table] or column in by_table[table]:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        log.info("init_db: added %s.%s", table, column)


def init_db(target: str | Path | None = None, watchlist_path: str | Path | None = None) -> None:
    """Create tables and seed the watchlist. Idempotent."""
    watchlist_path = Path(watchlist_path) if watchlist_path else settings.watchlist_path
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect(target) as conn:
        conn.executescript(schema_sql)
        _apply_column_additions(conn)
        _seed_watchlist(conn, watchlist_path)
    log.info("init_db complete: %s", describe(target))


def _seed_watchlist(conn, watchlist_path: Path) -> None:
    entries = json.loads(watchlist_path.read_text(encoding="utf-8"))
    rows = [
        (
            e["company_name"],
            e["tier"],
            e.get("sector"),
            e.get("notes"),
            json.dumps(e.get("aliases", [])),
        )
        for e in entries
    ]
    # ON CONFLICT DO UPDATE rather than SQLite's INSERT OR REPLACE: the upsert
    # form is understood by both engines, so there's one statement to maintain.
    conn.executemany(
        "INSERT INTO watchlist (company_name, tier, sector, notes, aliases) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (company_name) DO UPDATE SET "
        "tier = EXCLUDED.tier, sector = EXCLUDED.sector, "
        "notes = EXCLUDED.notes, aliases = EXCLUDED.aliases",
        rows,
    )
    log.info("watchlist seeded: %d entries", len(rows))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ingest(records: Iterable[dict], target: str | Path | None = None) -> int:
    """Insert raw scraped records into signals. Dedupe on source_url. Return inserted count."""
    inserted = 0
    skipped = 0
    with connect(target) as conn:
        for rec in records:
            raw = (rec.get("raw_content") or "").strip()
            if not raw:
                skipped += 1
                continue
            row = (
                rec.get("signal_id") or str(uuid.uuid4()),
                rec.get("source_type", "job_board"),
                rec.get("source_name", "pngworkforce"),
                rec.get("source_url"),
                rec.get("captured_at") or _now_iso(),
                rec.get("geography", "PNG"),
                rec.get("sector"),
                rec.get("company_name"),
                rec.get("watchlist_tier"),
                rec.get("signal_category"),
                rec.get("review_cycle"),
                raw,
                rec.get("analysis_notes"),
                int(bool(rec.get("is_new_prospect", 0))),
                rec.get("classified_at"),
            )
            # ON CONFLICT DO NOTHING rather than catching a duplicate-key error:
            # Postgres aborts the whole transaction on a constraint violation, so
            # letting one duplicate raise would discard every row inserted before
            # it. Letting the engine skip the row keeps the batch intact.
            # RETURNING tells us which branch was taken — it yields a row on
            # insert and nothing on skip, identically on both engines.
            cur = conn.execute(
                """INSERT INTO signals (
                    signal_id, source_type, source_name, source_url, captured_at,
                    geography, sector, company_name, watchlist_tier, signal_category,
                    review_cycle, raw_content, analysis_notes, is_new_prospect, classified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                RETURNING signal_id""",
                row,
            )
            if cur.fetchone() is not None:
                inserted += 1
            else:
                log.debug("dedupe skip: %s", rec.get("source_url"))
                skipped += 1
        conn.commit()
    log.info("ingest: inserted=%d skipped=%d", inserted, skipped)
    return inserted


def wipe_signals(target: str | Path | None = None) -> None:
    """Truncate signals table. Used by the KPI harness between runs."""
    with connect(target) as conn:
        conn.execute("DELETE FROM signals")
