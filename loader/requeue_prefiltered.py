"""Send back for classification the items the old blocklist dropped wrongly.

The blocklist used to search a whole advert for its words, so an employer
description mentioning "retail" or "hotel" dropped a relevant job, and news
stories were dropped too. It now checks a job ad's title only, and never news or
tenders (`agents.signal_analyst.prefilter`). Rows it dropped before that are
marked classified and never reach the model again; this finds the ones the new
rule would let through and clears their classification, so the next pipeline
run classifies them. It costs about one model call per 25 rows.

    python -m loader.requeue_prefiltered            # report only
    python -m loader.requeue_prefiltered --apply    # clear them for the next run
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from agents.signal_analyst import prefilter
from loader.db import connect, describe

log = logging.getLogger(__name__)


def candidates(target: str | Path | None = None) -> list[dict[str, Any]]:
    """Blocked rows the current rule would pass."""
    with connect(target, readonly=True) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT signal_id, source_name, source_type, raw_content FROM signals "
            "WHERE analysis_notes = 'prefiltered:blocklist'").fetchall()]
    return [r for r in rows if prefilter(r["raw_content"] or "", r["source_type"])[0]]


def requeue(target: str | Path | None = None, *, apply: bool = False) -> list[dict[str, Any]]:
    found = candidates(target)
    if apply and found:
        with connect(target) as conn:
            for r in found:
                # Back to how ingest left it: the prefilter filled these with
                # placeholders that the classifier would otherwise keep.
                conn.execute(
                    "UPDATE signals SET classified_at = NULL, sector = NULL, "
                    "signal_category = NULL, review_cycle = NULL, analysis_notes = NULL "
                    "WHERE signal_id = ? AND analysis_notes = 'prefiltered:blocklist'",
                    (r["signal_id"],),
                )
        log.info("requeue: %d row(s) cleared for classification", len(found))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="clear them; without it, report only")
    parser.add_argument("--db", default=None, help="override the database target")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print(f"database: {describe(args.db)}\n")
    found = requeue(args.db, apply=args.apply)
    for r in found:
        title = (r["raw_content"] or "").split("|", 1)[0].strip()
        print(f"  {r['source_name']:<16} {title[:80]}")
    print(f"\n{len(found)} blocked row(s) would now be classified."
          + ("" if args.apply else " Dry run — nothing written. Re-run with --apply."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
