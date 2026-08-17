"""Re-derive watchlist matches on already-classified signals. No LLM calls.

The watchlist match is a string comparison between `signals.company_name` and
`config/watchlist.json` — it never needed Gemini. So when the matching rules
change, or the watchlist itself does, the existing rows can simply be
recomputed instead of re-classifying them and spending quota.

    python -m loader.rematch --dry-run     # report what would change
    python -m loader.rematch               # apply

This exists because `fuzzy_match_watchlist` previously used `fuzz.WRatio`, whose
partial-ratio component matched short watchlist names against almost anything:
26 of 29 tiered companies in production were wrong. Only `watchlist_tier` and
`is_new_prospect` are touched — the Gemini-derived sector, category, review
cycle and analysis notes are left exactly as they were.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from agents.signal_analyst import _load_watchlist, fuzzy_match_watchlist
from config.settings import configure_logging
from loader.db import connect, describe

log = logging.getLogger(__name__)


def rematch(target: str | Path | None = None, *, dry_run: bool = False) -> dict:
    """Recompute watchlist_tier for every classified signal.

    Returns counts of what changed, keyed by transition.
    """
    with connect(target) as conn:
        watchlist = _load_watchlist(conn)
        if not watchlist:
            raise RuntimeError(
                "The watchlist table is empty — run `python -m loader.check --init` first."
            )
        rows = conn.execute(
            "SELECT signal_id, company_name, watchlist_tier, is_new_prospect "
            "FROM signals WHERE classified_at IS NOT NULL"
        ).fetchall()

    changes: list[tuple[str, str | None, bool]] = []
    transitions: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}

    for row in rows:
        signal_id, company, old_tier, old_new = row[0], row[1], row[2], row[3]
        matched, new_tier = fuzzy_match_watchlist(company, watchlist)

        named = bool(company and company.strip() and company.strip().lower() != "unknown")
        # A watchlist client is an existing relationship, not a prospect. A row
        # whose employer the classifier could not identify is neither: there is
        # nobody to approach, so it must not be flagged as a new prospect.
        new_is_new = 1 if (named and not matched) else 0

        if new_tier == old_tier and int(bool(old_new)) == new_is_new:
            continue

        kind = (
            "removed" if old_tier and not new_tier
            else "added" if new_tier and not old_tier
            else "retiered" if new_tier != old_tier
            else "prospect_flag"
        )
        transitions[kind] += 1
        examples.setdefault(kind, [])
        if len(examples[kind]) < 6:
            label = company if (company and company.strip()) else "(no company name)"
            examples[kind].append(f"{label} ({old_tier or '-'} -> {new_tier or '-'})")
        changes.append((signal_id, new_tier, bool(new_is_new)))

    summary = {
        "scanned": len(rows),
        "changed": len(changes),
        **{k: v for k, v in transitions.items()},
    }

    for kind, rows_shown in examples.items():
        log.info("%s: %s", kind, "; ".join(rows_shown))

    if dry_run or not changes:
        log.info("rematch: %s%s", summary, " (dry run — nothing written)" if dry_run else "")
        return summary

    with connect(target) as conn:
        for signal_id, tier, is_new in changes:
            conn.execute(
                "UPDATE signals SET watchlist_tier = ?, is_new_prospect = ? WHERE signal_id = ?",
                (tier, int(is_new), signal_id),
            )
    log.info("rematch: applied %d changes to %s", len(changes), describe(target))
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Recompute watchlist matches without calling Gemini")
    p.add_argument("--db", default=None, help="override the database target")
    p.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = p.parse_args(argv)

    configure_logging()
    try:
        s = rematch(args.db, dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        f"\nscanned={s['scanned']} changed={s['changed']} "
        f"removed={s.get('removed', 0)} added={s.get('added', 0)} "
        f"retiered={s.get('retiered', 0)} prospect_flag={s.get('prospect_flag', 0)}"
        + ("  (dry run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
