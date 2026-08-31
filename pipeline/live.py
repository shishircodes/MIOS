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
from api.digest_service import build_digest_payload
from delivery.digest import build_digest
from delivery.pulse import generate_pulse, save_pulse
from delivery.slack import post_digest
from loader import run_log
from loader.db import connect, describe, resolve_target
from loader.digest_archive import save_digest
from loader.ingest import ingest, init_db
from loader.source_settings import enabled_sources
from scraper import SOURCE_NAMES, scrape_all

log = logging.getLogger(__name__)

#: Records to take from each source per run. Per-source rather than a total
#: budget, so one prolific board cannot crowd out a quiet one. The admin screen
#: quotes this figure, so it lives here rather than being repeated.
DEFAULT_SCRAPE_LIMIT = 50


def _classified_for_run(db_path: str | Path, run_id: str | None) -> int:
    """How many classified signals this run collected.

    Counted rather than inferred from the ingest total: a run can insert rows
    that never get classified — a spent Gemini quota does exactly that — and
    those cannot appear in a digest.
    """
    if not run_id:
        return 0
    try:
        with connect(db_path, readonly=True) as conn:
            return int(conn.execute(
                "SELECT count(*) FROM signals WHERE run_id = ? AND classified_at IS NOT NULL",
                (run_id,),
            ).fetchone()[0] or 0)
    except Exception as exc:  # noqa: BLE001 - treat as "nothing of its own"
        log.warning("live: could not count this run's signals (%s)", exc)
        return 0


def run_live_cycle(
    *,
    scrape_limit: int = DEFAULT_SCRAPE_LIMIT,
    digest_window_days: int = 7,
    base_url: str | None = None,
    sources: list[str] | None = None,
    db_path: str | Path | None = None,
    do_scrape: bool = True,
    do_slack: bool = True,
    do_pulse: bool = True,
    run_id: str | None = None,
    gemini_caller: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one production-style cycle.

    `run_id` identifies the pipeline run this cycle belongs to. The scheduler
    and the Admin panel already claim one before calling, and pass it in; a
    command-line run claims its own here so that every cycle is recorded and
    every signal can be traced to the run that collected it. That is what lets
    a digest cover exactly one run.

    `gemini_caller` exists for tests — leave None to use the real Gemini client.
    Returns a summary dict with scraped/ingested/classified counts and the
    digest text, plus a `slack_ok` boolean (False if Slack was skipped).
    """
    # resolve_target, not Path(): wrapping in Path would turn a Neon DSN into a
    # (nonsensical) file path and silently write to SQLite instead.
    db_path = resolve_target(db_path)
    init_db(db_path)

    # A cycle started from the command line has no run behind it, so it opens
    # one. Tolerated rather than required: an unwritable run log should not stop
    # a scrape, it should only cost this cycle its place in the archive.
    own_run = False
    if run_id is None:
        try:
            run_id = run_log.claim(trigger=run_log.TRIGGER_CLI, target=db_path)
            own_run = True
        except run_log.RunInProgress:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("live: could not open a run record (%s) — this cycle will not "
                        "be archived", exc)

    scraped = 0
    inserted = 0
    scraped_by_source: dict[str, int] = {}
    if do_scrape:
        # An explicit --source wins over the stored selection: asking for a
        # specific source on the command line is a deliberate override, not a
        # default to be second-guessed.
        chosen = sources if sources else enabled_sources(db_path)

        if not chosen:
            # Deliberately not passed down to `scrape_all`, which reads an empty
            # list as falsy and would fall back to *every* source — the exact
            # opposite of what an administrator asked for by switching them all
            # off. Skipping here is the only safe reading of "none enabled".
            log.warning(
                "live: every source is switched off in the Admin panel — skipping the "
                "fetch. Re-enable one under Admin > Sources, or pass --source to override."
            )
            records = []
        else:
            if sources is None and set(chosen) != set(SOURCE_NAMES):
                log.info("live: collecting from %s (others switched off in Admin)",
                         ", ".join(chosen))
            records = scrape_all(limit=scrape_limit, sources=chosen, base_url=base_url)
        scraped = len(records)
        scraped_by_source = dict(Counter(r.get("source_name", "unknown") for r in records))
        log.info("live: scraped %d postings %s", scraped, scraped_by_source)
        if records:
            inserted = ingest(records, db_path, run_id=run_id)
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

    # Did this run produce anything publishable? Collecting is not enough — rows
    # that were never classified cannot appear in a digest, which is exactly
    # what a spent Gemini quota leaves behind.
    own_signals = _classified_for_run(db_path, run_id)

    # A run with nothing of its own is not archived, and its digest falls back to
    # the window. Two failures reach here — the scrape returned nothing, or
    # nothing could be classified — and in both the team should still get the
    # week's figures rather than a blank message. What must not happen is an
    # archive entry claiming this run published a week it had no part in.
    digest_run = run_id if own_signals else None
    if run_id and not own_signals:
        log.warning("live: run %s produced no classified signals — posting the rolling "
                    "window instead, and archiving nothing for it", run_id)

    # One payload, feeding the Market Pulse, the stored digest and the Slack
    # message — so the three cannot disagree about what the week contained, or
    # about the window key they file it under.
    #
    # Market Pulse costs a Gemini call, so it is produced here, once per run, and
    # stored. The dashboard reads the stored row; it never generates.
    payload = build_digest_payload(db_path, days=digest_window_days, run_id=digest_run)

    pulse = None
    if do_pulse:
        outcome = generate_pulse(payload, target=db_path)
        save_pulse(
            outcome,
            window_from=payload.get("collectedFrom") or since.isoformat(timespec="seconds"),
            window_to=payload.get("collectedTo")
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            target=db_path,
        )
        if outcome.ok:
            pulse = outcome.bullets
            log.info("live: Market Pulse generated (%d bullets)", len(pulse))
        else:
            # Deliberately not fatal, and deliberately not replaced with computed
            # bullets: the section is simply absent this week.
            log.warning("live: Market Pulse not generated — %s", outcome.note)
    else:
        log.info("live: --no-pulse; skipping Market Pulse generation")

    digest_text = build_digest(db_path, since=since, pulse=pulse, run_id=digest_run)

    # The archive entry. Written after the digest text so the stored payload and
    # the message that went to Slack describe the same signals.
    if digest_run is not None:
        try:
            save_digest(
                run_id=digest_run,
                payload=payload,
                window_from=payload.get("collectedFrom") or since.isoformat(timespec="seconds"),
                window_to=(payload.get("collectedTo")
                           or datetime.now(timezone.utc).isoformat(timespec="seconds")),
                digest_text=digest_text,
                target=db_path,
            )
        except Exception as exc:  # noqa: BLE001 - the run still succeeded
            log.error("live: could not store the digest for run %s (%s)", run_id, exc)

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
        "run_id": run_id,
        #: False when the run produced nothing of its own, so the digest above
        #: is the rolling window rather than this run's work.
        "archived": digest_run is not None,
    }
    log.info(
        "live: cycle done — scraped=%d ingested=%d classified=%d digest=%d chars slack=%s",
        scraped, inserted, classified, summary["digest_chars"],
        "ok" if slack_ok else ("skipped" if not do_slack else "failed"),
    )
    # Only a run this function opened is closed here. One handed in belongs to
    # the caller, which records the outcome including any failure this cannot
    # see.
    if own_run and run_id:
        run_log.finish(run_id, status=run_log.STATUS_OK, collected=scraped, target=db_path)
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
    p.add_argument("--no-pulse", action="store_true",
                   help="skip the Market Pulse generation (saves one Gemini call)")
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
        do_pulse=not args.no_pulse,
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
