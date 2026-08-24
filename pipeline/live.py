"""Live production-style cycle: scrape -> ingest -> classify -> digest -> Slack.

Distinct from `evaluation.kpi_harness`, which loads labelled synthetic data and
scores classification accuracy. This module runs against the real source and
posts the resulting digest, with no scoring (no labels available for scraped
postings).

Usage:
    python -m pipeline.live                       # full cycle: all sources + classify + Slack
    python -m pipeline.live --limit 20            # scrape up to 20 postings per source
    python -m pipeline.live --source seek         # one source only
    python -m pipeline.live --no-scrape           # only classify pending + post
    python -m pipeline.live --no-slack            # skip Slack delivery
    python -m pipeline.live --days 14             # widen digest window
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from agents.signal_analyst import classify_pending
from config.settings import configure_logging, settings
from delivery.digest import build_digest
from delivery.slack import post_digest
from loader.db import describe, resolve_target
from loader.ingest import ingest, init_db
from scraper import SOURCE_NAMES, scrape_all

log = logging.getLogger(__name__)

#: Records to take from each source per run. Per-source rather than a total
#: budget, so one prolific board cannot crowd out a quiet one. The admin screen
#: quotes this figure, so it lives here rather than being repeated.
DEFAULT_SCRAPE_LIMIT = 50


def run_live_cycle(
    *,
    scrape_limit: int = DEFAULT_SCRAPE_LIMIT,
    digest_window_days: int = 7,
    base_url: str | None = None,
    sources: list[str] | None = None,
    db_path: str | Path | None = None,
    do_scrape: bool = True,
    do_slack: bool = True,
    gemini_caller: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one production-style cycle.

    `gemini_caller` exists for tests — leave None to use the real Gemini client.
    Returns a summary dict with scraped/ingested/classified counts and the
    digest text, plus a `slack_ok` boolean (False if Slack was skipped).
    """
    # resolve_target, not Path(): wrapping in Path would turn a Neon DSN into a
    # (nonsensical) file path and silently write to SQLite instead.
    db_path = resolve_target(db_path)
    init_db(db_path)

    scraped = 0
    inserted = 0
    scraped_by_source: dict[str, int] = {}
    if do_scrape:
        records = scrape_all(limit=scrape_limit, sources=sources, base_url=base_url)
        scraped = len(records)
        scraped_by_source = dict(Counter(r.get("source_name", "unknown") for r in records))
        log.info("live: scraped %d postings %s", scraped, scraped_by_source)
        if records:
            inserted = ingest(records, db_path)
        else:
            log.warning("live: scraper returned 0 records — continuing on existing pending rows")
    else:
        log.info("live: --no-scrape; skipping fetch")

    classify_counts = classify_pending(
        db_path,
        # No batch cap: classify whatever is pending, bounded by the daily Gemini
        # quota. The old `scrape_limit * 2` assumed two sources and left 50 rows
        # unclassified per run once a third was added.
        gemini_caller=gemini_caller,
    )
    classified = int(classify_counts.get("classified", 0))

    since = datetime.now(timezone.utc) - timedelta(days=digest_window_days)
    digest_text = build_digest(db_path, since=since)

    slack_ok = False
    if do_slack:
        if not settings.slack_webhook_url or settings.slack_webhook_url.endswith("..."):
            log.warning("live: SLACK_WEBHOOK_URL not configured — skipping Slack delivery")
        else:
            slack_ok = post_digest(settings.slack_webhook_url, digest_text)
            log.info("live: Slack delivery: %s", "ok" if slack_ok else "failed")
    else:
        log.info("live: --no-slack; skipping Slack delivery")

    summary = {
        "database": describe(db_path),
        "scraped": scraped,
        "scraped_by_source": scraped_by_source,
        "ingested": inserted,
        "classified": classified,
        "filtered_blocklist": int(classify_counts.get("filtered_blocklist", 0)),
        "filtered_too_short": int(classify_counts.get("filtered_too_short", 0)),
        "errors": int(classify_counts.get("errors", 0)),
        "digest_chars": len(digest_text),
        "slack_ok": slack_ok,
        "digest": digest_text,
    }
    log.info(
        "live: cycle done — scraped=%d ingested=%d classified=%d digest=%d chars slack=%s",
        scraped, inserted, classified, summary["digest_chars"],
        "ok" if slack_ok else ("skipped" if not do_slack else "failed"),
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MIOS live production cycle")
    p.add_argument("--limit", type=int, default=DEFAULT_SCRAPE_LIMIT,
                   help=f"per-source scrape limit (default {DEFAULT_SCRAPE_LIMIT})")
    p.add_argument(
        "--source", action="append", choices=list(SOURCE_NAMES), default=None,
        help="scrape only this source (repeatable; default: all)",
    )
    p.add_argument("--days", type=int, default=7, help="digest window in days (default 7)")
    p.add_argument("--no-scrape", action="store_true", help="skip scraping; classify pending only")
    p.add_argument("--no-slack", action="store_true", help="skip Slack delivery")
    p.add_argument("--db", type=str, default=None, help="override DB path")
    p.add_argument("--base-url", type=str, default=None, help="override scraper base URL")
    args = p.parse_args(argv)

    configure_logging()
    summary = run_live_cycle(
        scrape_limit=args.limit,
        digest_window_days=args.days,
        base_url=args.base_url,
        sources=args.source,
        db_path=args.db,
        do_scrape=not args.no_scrape,
        do_slack=not args.no_slack,
    )

    by_source = ",".join(f"{k}={v}" for k, v in sorted(summary["scraped_by_source"].items()))
    print(
        f"\nscraped={summary['scraped']}"
        f"{' (' + by_source + ')' if by_source else ''} "
        f"ingested={summary['ingested']} "
        f"classified={summary['classified']} "
        f"filtered={summary['filtered_blocklist'] + summary['filtered_too_short']} "
        f"errors={summary['errors']} "
        f"digest={summary['digest_chars']} chars "
        f"slack_ok={summary['slack_ok']}"
    )
    # Exit 0 unless absolutely nothing happened.
    if (summary["scraped"] == 0 and summary["classified"] == 0
            and summary["digest_chars"] == 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
