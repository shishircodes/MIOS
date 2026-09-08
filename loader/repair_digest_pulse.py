"""Attach a generated Market Pulse to an archived digest that was stored without one.

`pipeline.live` built the digest payload before generating the pulse, so the
archived copy recorded `marketPulse: None` for every run that completed in a
single pass. The pulse itself was generated, stored in `digest_pulse` and sent
to Slack — only the archived payload, which is what `/api/digest` serves,
was missing it.

That is fixed forward, but a digest already written keeps the gap until its run
is superseded. This repairs those rows in place.

**It invents nothing.** A row is only touched when a pulse exists in
`digest_pulse` whose window falls inside that digest's own window and whose
status is `generated` — the same lookup `/api/digest` performs. Where no such
pulse exists the digest is left exactly as it is, because a week that genuinely
produced no pulse must keep saying so.

Run with `--apply` to write. Without it, the script reports what it would change
and touches nothing.

    python -m loader.repair_digest_pulse            # dry run
    python -m loader.repair_digest_pulse --apply    # write
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from delivery.pulse import load_pulse
from loader.db import backend_label, connect

log = logging.getLogger(__name__)


def _rows(target: str | Path | None) -> list[dict[str, Any]]:
    with connect(target, readonly=True) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT run_id, window_from, window_to, generated_at, payload "
            "FROM digests ORDER BY window_to").fetchall()]


def plan(target: str | Path | None = None) -> list[dict[str, Any]]:
    """Which archived digests are missing a pulse that exists for their window."""
    out = []
    for row in _rows(target):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            log.warning("repair: run %s has an unreadable payload — skipping", row["run_id"])
            continue
        if payload.get("marketPulse"):
            continue

        window_from = payload.get("collectedFrom") or row["window_from"]
        window_to = payload.get("collectedTo") or row["window_to"]
        pulse = load_pulse(window_from, window_to, target)
        out.append({
            "run_id": row["run_id"],
            "window_to": row["window_to"],
            "pulse": pulse,
            "payload": payload,
        })
    return out


def apply(target: str | Path | None = None) -> int:
    """Write the repairs. Returns how many rows were changed."""
    changed = 0
    for item in plan(target):
        if not item["pulse"]:
            continue
        payload = dict(item["payload"])
        payload["marketPulse"] = item["pulse"]
        body = json.dumps(payload, separators=(",", ":"))
        with connect(target) as conn:
            # Only the payload. `generated_at`, `signal_count` and `digest_text`
            # are left alone: this corrects what was stored, it does not restate
            # when the digest was produced or what was sent to Slack.
            conn.execute("UPDATE digests SET payload = ? WHERE run_id = ?",
                         (body, item["run_id"]))
        log.info("repair: attached %d pulse bullets to run %s",
                 len(item["pulse"]["bullets"]), item["run_id"])
        changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without it, report only")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print(f"database: {backend_label()}\n")
    items = plan()
    if not items:
        print("Every archived digest either carries its Market Pulse or never had one.")
        return 0

    repairable = [i for i in items if i["pulse"]]
    for i in items:
        if i["pulse"]:
            print(f"  REPAIRABLE  run {i['run_id'][:12]}  window to {i['window_to'][:10]}  "
                  f"-> {len(i['pulse']['bullets'])} bullets, "
                  f"{i['pulse']['signalsAnalysed']} signals analysed")
        else:
            print(f"  leave alone run {i['run_id'][:12]}  window to {i['window_to'][:10]}  "
                  f"-> no pulse was generated for this window")

    print(f"\n{len(repairable)} of {len(items)} digests without a pulse can be repaired.")
    if not args.apply:
        print("Dry run — nothing written. Re-run with --apply to write.")
        return 0

    changed = apply()
    print(f"\nUpdated {changed} digest row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
