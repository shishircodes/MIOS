"""KPI harness. Runs the full classification pipeline N times against a
labelled ground-truth set and writes results.csv + a markdown KPI table.

Usage:
    python -m evaluation.kpi_harness                # 5 runs, post digest to Slack
    python -m evaluation.kpi_harness --runs 1       # single run
    python -m evaluation.kpi_harness --no-slack     # skip Slack delivery
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from agents.signal_analyst import classify_pending
from config.settings import REPO_ROOT, configure_logging, settings
from delivery.digest import build_digest
from delivery.slack import post_digest
from loader.ingest import init_db, ingest, wipe_signals

log = logging.getLogger(__name__)

RESULTS_CSV = REPO_ROOT / "results.csv"

# KPI targets (from §5.2 of the report).
KPI_TARGETS = {
    "signal_classification_accuracy_pct": 80.0,
    "watchlist_match_precision_pct": 90.0,
    "end_to_end_latency_seconds": 60.0,
    "api_cost_aud": 0.0,
}


# --------------------------------------------------------------------------
# Ground-truth loading
# --------------------------------------------------------------------------


def load_ground_truth(path: str | Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _to_signal_record(gt: dict[str, Any]) -> dict[str, Any]:
    """Adapt a ground-truth row to the loader.ingest record shape."""
    return {
        "signal_id": gt["id"],
        "source_url": gt.get("source_url") or f"syn://{gt['id']}",
        "raw_content": gt["raw_text"],
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "geography": "PNG",
        "source_type": "job_board",
        "source_name": "synthetic",
    }


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _classified_rows(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT signal_id, company_name, sector, signal_category, review_cycle, "
            "watchlist_tier, is_new_prospect FROM signals "
            "WHERE classified_at IS NOT NULL"
        ).fetchall()


def score_run(db_path: Path, ground_truth: list[dict[str, Any]]) -> dict[str, float]:
    gt_by_id = {g["id"]: g for g in ground_truth}
    rows = _classified_rows(db_path)
    if not rows:
        return {"classification_accuracy_pct": 0.0, "watchlist_precision_pct": 0.0,
                "n_scored": 0.0, "n_predicted_watchlist": 0.0}

    correct = 0
    pred_wl_total = 0
    pred_wl_correct = 0
    for row in rows:
        gt = gt_by_id.get(row["signal_id"])
        if not gt:
            continue
        if (row["signal_category"] == gt["ground_truth_signal_category"]
                and row["review_cycle"] == gt["ground_truth_review_cycle"]):
            correct += 1
        if row["watchlist_tier"] is not None:
            pred_wl_total += 1
            if (row["company_name"] == gt["ground_truth_watchlist_match"]
                    or _name_matches(row["company_name"], gt["ground_truth_watchlist_match"])):
                pred_wl_correct += 1

    n = len(rows)
    return {
        "classification_accuracy_pct": (correct / n) * 100.0,
        "watchlist_precision_pct": ((pred_wl_correct / pred_wl_total) * 100.0
                                    if pred_wl_total else 0.0),
        "n_scored": float(n),
        "n_predicted_watchlist": float(pred_wl_total),
    }


def _name_matches(predicted: str | None, expected: str | None) -> bool:
    if not predicted or not expected:
        return False
    p, e = predicted.lower(), expected.lower()
    return p == e or e in p or p in e


# --------------------------------------------------------------------------
# Run orchestration
# --------------------------------------------------------------------------


def run_evaluation(
    db_path: str | Path | None = None,
    ground_truth_path: str | Path | None = None,
    runs: int = 5,
) -> dict[str, Any]:
    db_path = Path(db_path or settings.db_path)
    gt_path = Path(ground_truth_path or settings.synthetic_postings_path)
    ground_truth = load_ground_truth(gt_path)
    log.info("KPI harness: %d runs against %d ground-truth records",
             runs, len(ground_truth))

    init_db(db_path)
    per_run: list[dict[str, Any]] = []

    for i in range(1, runs + 1):
        log.info("---- Run %d/%d ----", i, runs)
        wipe_signals(db_path)
        ingest([_to_signal_record(g) for g in ground_truth], db_path)

        t0 = time.perf_counter()
        counts = classify_pending(db_path, batch_size=len(ground_truth))
        latency = time.perf_counter() - t0

        scored = score_run(db_path, ground_truth)
        api_calls = int(counts.get("classified", 0)) + int(counts.get("errors", 0))
        per_run.append({
            "run": i,
            "classification_accuracy_pct": round(scored["classification_accuracy_pct"], 2),
            "watchlist_precision_pct": round(scored["watchlist_precision_pct"], 2),
            "latency_seconds": round(latency, 2),
            "api_calls": api_calls,
            "api_cost_aud": 0.0,  # gemini-2.5-flash free tier
            "filtered_too_short": int(counts.get("filtered_too_short", 0)),
            "filtered_blocklist": int(counts.get("filtered_blocklist", 0)),
            "errors": int(counts.get("errors", 0)),
            "n_scored": int(scored["n_scored"]),
            "n_predicted_watchlist": int(scored["n_predicted_watchlist"]),
        })

    aggregates = _aggregate(per_run)
    _write_csv(per_run, aggregates)
    _print_kpi_table(aggregates, runs)
    return {"per_run": per_run, "aggregates": aggregates}


def _aggregate(per_run: list[dict[str, Any]]) -> dict[str, float]:
    if not per_run:
        return {}
    return {
        "mean_classification_accuracy_pct": round(
            mean(r["classification_accuracy_pct"] for r in per_run), 2),
        "mean_watchlist_precision_pct": round(
            mean(r["watchlist_precision_pct"] for r in per_run), 2),
        "mean_latency_seconds": round(mean(r["latency_seconds"] for r in per_run), 2),
        "stdev_latency_seconds": round(
            pstdev(r["latency_seconds"] for r in per_run) if len(per_run) > 1 else 0.0, 2),
        "total_api_calls": sum(r["api_calls"] for r in per_run),
        "total_api_cost_aud": 0.0,
        "mean_filtered_pct": round(
            mean(((r["filtered_too_short"] + r["filtered_blocklist"]) /
                  max(r["filtered_too_short"] + r["filtered_blocklist"]
                      + r["n_scored"] + r["errors"], 1)) * 100.0
                 for r in per_run), 2),
    }


def _write_csv(per_run: list[dict[str, Any]], aggregates: dict[str, float]) -> None:
    if not per_run:
        return
    fieldnames = list(per_run[0].keys())
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in per_run:
            w.writerow(row)
        # Trailing aggregate row (run="aggregate")
        agg_row = {k: "" for k in fieldnames}
        agg_row["run"] = "aggregate"
        agg_row["classification_accuracy_pct"] = aggregates["mean_classification_accuracy_pct"]
        agg_row["watchlist_precision_pct"] = aggregates["mean_watchlist_precision_pct"]
        agg_row["latency_seconds"] = aggregates["mean_latency_seconds"]
        agg_row["api_calls"] = aggregates["total_api_calls"]
        agg_row["api_cost_aud"] = aggregates["total_api_cost_aud"]
        w.writerow(agg_row)
    log.info("results written: %s", RESULTS_CSV)


def _print_kpi_table(agg: dict[str, float], runs: int) -> None:
    rows = [
        ("Signal classification accuracy",
         f">= {KPI_TARGETS['signal_classification_accuracy_pct']:.0f}%",
         f"{agg['mean_classification_accuracy_pct']:.2f}%",
         "PASS" if agg["mean_classification_accuracy_pct"] >= KPI_TARGETS["signal_classification_accuracy_pct"] else "FAIL"),
        ("Watchlist match precision",
         f">= {KPI_TARGETS['watchlist_match_precision_pct']:.0f}%",
         f"{agg['mean_watchlist_precision_pct']:.2f}%",
         "PASS" if agg["mean_watchlist_precision_pct"] >= KPI_TARGETS["watchlist_match_precision_pct"] else "FAIL"),
        ("End-to-end batch latency (mean)",
         f"<= {KPI_TARGETS['end_to_end_latency_seconds']:.0f}s",
         f"{agg['mean_latency_seconds']:.2f}s "
         f"(stdev {agg['stdev_latency_seconds']:.2f}s)",
         "PASS" if agg["mean_latency_seconds"] <= KPI_TARGETS["end_to_end_latency_seconds"] else "FAIL"),
        ("API cost per run (Gemini)",
         "A$0.00 (free tier)",
         f"A$0.00 ({agg['total_api_calls']} calls / {runs} runs)",
         "PASS"),
        ("Slack digest quality",
         "Reviewer rating >= 4/5",
         "[manual review pending]",
         "TBC"),
    ]
    print("\n## Section 5.2 KPI Results\n")
    print("| KPI | Target | Achieved | Status |")
    print("| --- | --- | --- | --- |")
    for kpi, target, achieved, status in rows:
        print(f"| {kpi} | {target} | {achieved} | {status} |")
    print(f"\nMean pre-filter drop rate: {agg['mean_filtered_pct']:.2f}%\n")


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MIOS PoC KPI harness")
    p.add_argument("--runs", type=int, default=5, help="evaluation runs (default 5)")
    p.add_argument("--no-slack", action="store_true", help="skip posting digest to Slack")
    p.add_argument("--db", type=str, default=None, help="override DB path")
    p.add_argument("--ground-truth", type=str, default=None, help="override ground-truth jsonl path")
    args = p.parse_args(argv)

    configure_logging()
    result = run_evaluation(db_path=args.db, ground_truth_path=args.ground_truth, runs=args.runs)

    if not args.no_slack:
        if not settings.slack_webhook_url or settings.slack_webhook_url.endswith("..."):
            log.warning("SLACK_WEBHOOK_URL not configured — skipping Slack delivery")
        else:
            digest = build_digest(
                db_path=settings.db_path,
                since=datetime.now(timezone.utc) - timedelta(days=7),
            )
            ok = post_digest(settings.slack_webhook_url, digest)
            log.info("Slack delivery: %s", "ok" if ok else "failed")

    return 0 if result["per_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
