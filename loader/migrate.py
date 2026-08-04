"""Copy an existing SQLite database into Neon PostgreSQL.

Usage:
    python -m loader.migrate                       # data/mios.db -> $DATABASE_URL
    python -m loader.migrate --source data/old.db  # pick a different source
    python -m loader.migrate --dry-run             # report what would move
    python -m loader.migrate --wipe                # clear the target first

Idempotent: rows are inserted with ON CONFLICT DO NOTHING, so re-running after a
partial failure tops up the target rather than erroring or duplicating.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from config.settings import configure_logging, settings
from loader.db import SCHEMA_PATH, connect, describe, is_postgres, resolve_target

log = logging.getLogger(__name__)

SIGNAL_COLUMNS = [
    "signal_id", "source_type", "source_name", "source_url", "captured_at",
    "geography", "sector", "company_name", "watchlist_tier", "signal_category",
    "review_cycle", "raw_content", "analysis_notes", "is_new_prospect", "classified_at",
]
WATCHLIST_COLUMNS = ["company_name", "tier", "sector", "notes", "aliases"]

#: Copied in batches so a large signals table doesn't build one enormous statement.
BATCH_SIZE = 500


def _read_all(source: Path, table: str, columns: list[str]) -> list[tuple]:
    with connect(source) as conn:
        rows = conn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
    return [tuple(r[c] for c in columns) for r in rows]


def _count(target: str | Path, table: str) -> int:
    with connect(target) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _copy(target: str | Path, table: str, columns: list[str], rows: list[tuple]) -> int:
    """Insert rows, skipping any that already exist. Returns rows actually written."""
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in columns)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        "ON CONFLICT DO NOTHING"
    )
    before = _count(target, table)
    with connect(target) as conn:
        for i in range(0, len(rows), BATCH_SIZE):
            conn.executemany(sql, rows[i:i + BATCH_SIZE])
            log.info("  %s: %d/%d", table, min(i + BATCH_SIZE, len(rows)), len(rows))
    return _count(target, table) - before


def migrate(
    source: str | Path | None = None,
    target: str | Path | None = None,
    *,
    dry_run: bool = False,
    wipe: bool = False,
) -> dict[str, int]:
    """Copy watchlist + signals from a SQLite file into the target database."""
    source = Path(source or settings.db_path)
    resolved_target = resolve_target(target)

    if not source.exists():
        raise FileNotFoundError(f"source database not found: {source}")
    if is_postgres(source):
        raise ValueError("source must be a SQLite file, not a Postgres DSN")
    if not is_postgres(resolved_target):
        raise ValueError(
            "target is not a Postgres database. Set DATABASE_URL to your Neon "
            "connection string, or pass --target."
        )

    watchlist = _read_all(source, "watchlist", WATCHLIST_COLUMNS)
    signals = _read_all(source, "signals", SIGNAL_COLUMNS)
    log.info("source %s: %d watchlist, %d signals", source, len(watchlist), len(signals))
    log.info("target %s", describe(resolved_target))

    if dry_run:
        log.info("--dry-run: nothing written")
        return {"watchlist_read": len(watchlist), "signals_read": len(signals),
                "watchlist_written": 0, "signals_written": 0}

    # Safe on an existing database: the DDL is all IF NOT EXISTS.
    with connect(resolved_target) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    if wipe:
        with connect(resolved_target) as conn:
            conn.execute("DELETE FROM signals")
            conn.execute("DELETE FROM watchlist")
        log.warning("--wipe: cleared target tables before copying")

    # Watchlist first: signals reference companies conceptually, and a partially
    # migrated database is more useful with the reference data already present.
    wl_written = _copy(resolved_target, "watchlist", WATCHLIST_COLUMNS, watchlist)
    sig_written = _copy(resolved_target, "signals", SIGNAL_COLUMNS, signals)

    summary = {
        "watchlist_read": len(watchlist),
        "signals_read": len(signals),
        "watchlist_written": wl_written,
        "signals_written": sig_written,
        "watchlist_total": _count(resolved_target, "watchlist"),
        "signals_total": _count(resolved_target, "signals"),
    }
    log.info("migration complete: %s", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Copy the MIOS SQLite database into Neon PostgreSQL")
    p.add_argument("--source", default=None, help="SQLite file to read (default: DB_PATH)")
    p.add_argument("--target", default=None, help="Postgres DSN (default: DATABASE_URL)")
    p.add_argument("--dry-run", action="store_true", help="report counts without writing")
    p.add_argument("--wipe", action="store_true", help="clear target tables first")
    args = p.parse_args(argv)

    configure_logging()
    try:
        s = migrate(args.source, args.target, dry_run=args.dry_run, wipe=args.wipe)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"\nwatchlist: read {s['watchlist_read']} -> wrote {s['watchlist_written']}")
    print(f"signals:   read {s['signals_read']} -> wrote {s['signals_written']}")
    if not args.dry_run:
        print(f"target now holds {s['watchlist_total']} watchlist / {s['signals_total']} signals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
