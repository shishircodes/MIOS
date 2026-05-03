"""SQLite ingestion. Loads schema, watchlist, and raw scraped signals."""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from config.settings import REPO_ROOT, settings

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path, watchlist_path: str | Path | None = None) -> None:
    """Create tables and seed the watchlist. Idempotent."""
    watchlist_path = Path(watchlist_path) if watchlist_path else settings.watchlist_path
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with _connect(db_path) as conn:
        conn.executescript(schema_sql)
        _seed_watchlist(conn, watchlist_path)
        conn.commit()
    log.info("init_db complete: %s", db_path)


def _seed_watchlist(conn: sqlite3.Connection, watchlist_path: Path) -> None:
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
    conn.executemany(
        "INSERT OR REPLACE INTO watchlist (company_name, tier, sector, notes, aliases) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    log.info("watchlist seeded: %d entries", len(rows))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ingest(records: Iterable[dict], db_path: str | Path) -> int:
    """Insert raw scraped records into signals. Dedupe on source_url. Return inserted count."""
    inserted = 0
    skipped = 0
    with _connect(db_path) as conn:
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
            try:
                conn.execute(
                    """INSERT INTO signals (
                        signal_id, source_type, source_name, source_url, captured_at,
                        geography, sector, company_name, watchlist_tier, signal_category,
                        review_cycle, raw_content, analysis_notes, is_new_prospect, classified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    row,
                )
                inserted += 1
            except sqlite3.IntegrityError as exc:
                # Unique constraint on source_url => duplicate
                log.debug("dedupe skip (%s): %s", exc, rec.get("source_url"))
                skipped += 1
        conn.commit()
    log.info("ingest: inserted=%d skipped=%d", inserted, skipped)
    return inserted


def wipe_signals(db_path: str | Path) -> None:
    """Truncate signals table. Used by the KPI harness between runs."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM signals")
        conn.commit()
