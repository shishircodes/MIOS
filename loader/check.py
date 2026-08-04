"""Connectivity check for the configured database.

Run this first after setting DATABASE_URL — it isolates "can I reach Neon?" from
"does the pipeline work?", so a failure points at one thing instead of both.

    python -m loader.check
    python -m loader.check --init     # create the schema if it's missing
"""
from __future__ import annotations

import argparse
import sys
import time

from config.settings import configure_logging, settings
from loader.db import connect, describe, is_postgres, resolve_target

TABLES = ("signals", "watchlist")


def _server_version(conn) -> str:
    if conn.is_postgres:
        row = conn.execute("SELECT version()").fetchone()
        return str(row[0]).split(",")[0]
    row = conn.execute("SELECT sqlite_version()").fetchone()
    return f"SQLite {row[0]}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Check the MIOS database connection")
    p.add_argument("--init", action="store_true", help="create the schema if missing")
    args = p.parse_args(argv)

    configure_logging("WARNING")
    target = resolve_target()

    print(f"backend : {'PostgreSQL (Neon)' if is_postgres(target) else 'SQLite'}")
    print(f"target  : {describe()}")
    if not is_postgres(target) and settings.database_url == "":
        print("          (DATABASE_URL is not set — set it to use Neon)")

    started = time.perf_counter()
    try:
        with connect() as conn:
            elapsed = (time.perf_counter() - started) * 1000
            print(f"connect : OK in {elapsed:.0f} ms")
            print(f"server  : {_server_version(conn)}")

            if args.init:
                from loader.ingest import init_db
                init_db()
                print("schema  : created/verified")

            for table in TABLES:
                try:
                    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    print(f"{table:<8}: {n} rows")
                except Exception:
                    # Postgres aborts the transaction on a failed statement, so
                    # unwind before probing the next table.
                    conn.rollback()
                    print(f"{table:<8}: MISSING — run 'python -m loader.check --init'")
    except Exception as exc:  # noqa: BLE001 - this command exists to report failures
        print(f"\nconnect : FAILED\n{type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "\nCommon causes:\n"
            "  - DATABASE_URL missing the ?sslmode=require Neon needs\n"
            "  - password not URL-encoded (@ : / must be percent-escaped)\n"
            "  - Neon project paused or the endpoint hostname is wrong\n"
            "  - copied the psql command instead of the connection string",
            file=sys.stderr,
        )
        return 1

    print("\nall good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
