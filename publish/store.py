"""Storage and the review workflow for quarterly reports.

The workflow is the point of this module, not the storage. §8.3 of the project
spec requires the human review step to be *architecturally enforced* rather than
merely expected, which shapes three decisions here:

* `approve_report` refuses while any section is unapproved or empty, so a report
  cannot reach `approved` by someone clicking one button.
* Editing a section clears its approval. Otherwise a reviewer could approve
  text, change it afterwards, and keep the tick.
* There is no `distribute`, `send` or `publish_external` function. Approval is
  the last thing MIOS does; getting the document to clients happens outside it,
  performed by a person.

Regenerating never overwrites an existing report — it creates a new draft. A
half-edited document is somebody's work in progress.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loader.db import connect
from publish.report import Section, generate, quarter_bounds
from publish.rewrite import rewrite

log = logging.getLogger(__name__)

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"


class ReportError(ValueError):
    """Bad request against a report. The message is shown to the user."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Creating
# --------------------------------------------------------------------------


def create_report(
    quarter: str,
    *,
    title: str | None = None,
    target: str | Path | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Generate a fresh draft for `quarter` and store it.

    The figures are always computed from the signals. Gemini then rewrites the
    wording, unless `use_llm` is off or the daily quota is gone — in which case
    the computed prose ships and `prose_note` says why.
    """
    try:
        quarter_bounds(quarter)
    except ValueError as exc:
        raise ReportError(str(exc)) from exc

    data, computed = generate(quarter, target)

    if use_llm:
        outcome = rewrite(computed, target=target)
        sections, prose_source, prose_note = (
            outcome.sections,
            "gemini" if outcome.used_llm else "computed",
            outcome.reason,
        )
    else:
        sections, prose_source, prose_note = computed, "computed", None

    # Pair each shipped section with the deterministic text it came from, so the
    # difference is visible rather than assumed.
    computed_by_heading = {s.heading: s.body for s in computed}
    report_id = f"rep-{uuid.uuid4().hex[:12]}"
    now = _now()
    heading = title or f"Industrial Workforce Trends — {quarter}"

    with connect(target) as conn:
        conn.execute(
            "INSERT INTO reports (report_id, quarter, title, status, generated_at, "
            "signals_analysed, window_from, window_to, prose_source, prose_note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (report_id, quarter, heading, STATUS_DRAFT, now, data.total,
             data.window_from, data.window_to, prose_source, prose_note),
        )
        for i, s in enumerate(sections):
            conn.execute(
                "INSERT INTO report_sections (section_id, report_id, position, heading, "
                "body, generated_body, computed_body, source, approved) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (f"sec-{uuid.uuid4().hex[:12]}", report_id, i, s.heading,
                 s.body, s.body, computed_by_heading.get(s.heading, s.body), s.source, 0),
            )

    log.info("publish: generated %s for %s (%d signals, %d sections, prose=%s)",
             report_id, quarter, data.total, len(sections), prose_source)
    return get_report(report_id, target=target)  # type: ignore[return-value]


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _section_to_api(row: Any) -> dict[str, Any]:
    body = row["body"] or ""
    return {
        "id": row["section_id"],
        "position": row["position"],
        "heading": row["heading"],
        "body": body,
        "source": row["source"],
        "approved": bool(row["approved"]),
        "approvedAt": row["approved_at"],
        "approvedBy": row["approved_by"],
        "editedAt": row["edited_at"],
        #: The deterministic wording this section came from. The reviewer can
        #: compare it against what shipped rather than taking the rewrite on trust.
        "computedBody": row["computed_body"] or body,
        "rewritten": (row["computed_body"] or body) != (row["generated_body"] or ""),
        #: True when a human has changed what the generator wrote. The reviewer
        #: needs to know which prose is theirs and which is the machine's.
        "edited": body != (row["generated_body"] or ""),
        #: A manual section starts empty and blocks approval until written.
        "empty": not body.strip(),
    }


def get_report(report_id: str, target: str | Path | None = None) -> dict[str, Any] | None:
    with connect(target) as conn:
        report = conn.execute(
            "SELECT report_id, quarter, title, status, generated_at, signals_analysed, "
            "window_from, window_to, approved_at, approved_by, prose_source, "
            "prose_note FROM reports "
            "WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        if report is None:
            return None
        sections = conn.execute(
            "SELECT section_id, report_id, position, heading, body, generated_body, "
            "computed_body, source, approved, approved_at, approved_by, edited_at "
            "FROM report_sections "
            "WHERE report_id = ? ORDER BY position",
            (report_id,),
        ).fetchall()

    shaped = [_section_to_api(s) for s in sections]
    outstanding = [s["heading"] for s in shaped if not s["approved"]]
    return {
        "id": report["report_id"],
        "quarter": report["quarter"],
        "title": report["title"],
        "status": report["status"],
        "generatedAt": report["generated_at"],
        "signalsAnalysed": report["signals_analysed"],
        "windowFrom": report["window_from"],
        "windowTo": report["window_to"],
        "approvedAt": report["approved_at"],
        "approvedBy": report["approved_by"],
        #: 'gemini' or 'computed' — whether a language model touched the wording.
        "proseSource": report["prose_source"] or "computed",
        #: Why the computed wording was kept, when it was.
        "proseNote": report["prose_note"],
        "sections": shaped,
        "sectionsApproved": sum(1 for s in shaped if s["approved"]),
        "sectionsTotal": len(shaped),
        #: What still stands between this draft and sign-off, named rather than
        #: left for the reviewer to hunt for.
        "outstanding": outstanding,
        "canApprove": bool(shaped) and not outstanding
                      and report["status"] != STATUS_APPROVED,
    }


def list_reports(limit: int = 50, target: str | Path | None = None) -> list[dict[str, Any]]:
    """Summaries, newest first. Section bodies are omitted — a list of reports
    does not need every word of every one."""
    with connect(target) as conn:
        rows = conn.execute(
            "SELECT r.report_id, r.quarter, r.title, r.status, r.generated_at, "
            "r.signals_analysed, r.prose_source, r.approved_at, r.approved_by, "
            "COUNT(s.section_id) AS total, "
            "COALESCE(SUM(s.approved), 0) AS approved "
            "FROM reports r LEFT JOIN report_sections s ON s.report_id = r.report_id "
            "GROUP BY r.report_id, r.quarter, r.title, r.status, r.generated_at, "
            "r.signals_analysed, r.prose_source, r.approved_at, r.approved_by "
            "ORDER BY r.generated_at DESC, r.report_id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    return [{
        "id": r["report_id"],
        "quarter": r["quarter"],
        "title": r["title"],
        "status": r["status"],
        "generatedAt": r["generated_at"],
        "signalsAnalysed": r["signals_analysed"],
        "proseSource": r["prose_source"] or "computed",
        "approvedAt": r["approved_at"],
        "approvedBy": r["approved_by"],
        "sectionsApproved": int(r["approved"] or 0),
        "sectionsTotal": int(r["total"] or 0),
    } for r in rows]


# --------------------------------------------------------------------------
# Editing and approving
# --------------------------------------------------------------------------


def _load_section(conn: Any, section_id: str) -> Any:
    row = conn.execute(
        "SELECT section_id, report_id, body, approved FROM report_sections "
        "WHERE section_id = ?", (section_id,)
    ).fetchone()
    if row is None:
        raise ReportError("No such section.")
    return row


def _assert_not_approved(conn: Any, report_id: str) -> None:
    status = conn.execute(
        "SELECT status FROM reports WHERE report_id = ?", (report_id,)
    ).fetchone()
    if status is not None and status["status"] == STATUS_APPROVED:
        raise ReportError(
            "This report has been approved and can no longer be changed. "
            "Generate a new draft if it needs revising."
        )


def update_section(
    section_id: str,
    body: str,
    target: str | Path | None = None,
) -> dict[str, Any]:
    """Replace a section's text. Approval is cleared: text that changed after
    sign-off has not been signed off."""
    with connect(target) as conn:
        row = _load_section(conn, section_id)
        _assert_not_approved(conn, row["report_id"])
        conn.execute(
            "UPDATE report_sections SET body = ?, edited_at = ?, approved = 0, "
            "approved_at = NULL, approved_by = NULL WHERE section_id = ?",
            (body, _now(), section_id),
        )
        report_id = row["report_id"]
    log.info("publish: section %s edited (approval cleared)", section_id)
    return get_report(report_id, target=target)  # type: ignore[return-value]


def set_section_approval(
    section_id: str,
    approved: bool,
    reviewer: str,
    target: str | Path | None = None,
) -> dict[str, Any]:
    with connect(target) as conn:
        row = _load_section(conn, section_id)
        _assert_not_approved(conn, row["report_id"])
        if approved and not (row["body"] or "").strip():
            raise ReportError("An empty section cannot be approved — write it first.")
        conn.execute(
            "UPDATE report_sections SET approved = ?, approved_at = ?, approved_by = ? "
            "WHERE section_id = ?",
            (1 if approved else 0, _now() if approved else None,
             reviewer if approved else None, section_id),
        )
        report_id = row["report_id"]
    return get_report(report_id, target=target)  # type: ignore[return-value]


def approve_report(
    report_id: str,
    reviewer: str,
    target: str | Path | None = None,
) -> dict[str, Any]:
    """Sign off the whole report.

    Refuses while anything is outstanding. This is the enforcement §8.3 asks
    for: approval is the sum of the section reviews, not a shortcut past them.
    """
    report = get_report(report_id, target=target)
    if report is None:
        raise ReportError("No such report.")
    if report["status"] == STATUS_APPROVED:
        raise ReportError("This report is already approved.")
    if report["outstanding"]:
        raise ReportError(
            "Every section must be approved first. Still outstanding: "
            + ", ".join(report["outstanding"])
        )

    with connect(target) as conn:
        conn.execute(
            "UPDATE reports SET status = ?, approved_at = ?, approved_by = ? "
            "WHERE report_id = ?",
            (STATUS_APPROVED, _now(), reviewer, report_id),
        )
    log.info("publish: report %s approved by %s", report_id, reviewer)
    return get_report(report_id, target=target)  # type: ignore[return-value]


def delete_report(report_id: str, target: str | Path | None = None) -> bool:
    with connect(target) as conn:
        conn.execute("DELETE FROM report_sections WHERE report_id = ?", (report_id,))
        cur = conn.execute("DELETE FROM reports WHERE report_id = ?", (report_id,))
        removed = (cur.rowcount or 0) > 0
    if removed:
        log.info("publish: deleted report %s", report_id)
    return removed


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def render_markdown(report: dict[str, Any]) -> str:
    """The report as Markdown, for handing to whoever distributes it.

    A draft says so at the top. An unapproved document that leaves the building
    looking finished is the failure mode the review step exists to prevent.
    """
    lines = [f"# {report['title']}", ""]
    if report["status"] != STATUS_APPROVED:
        lines += ["> **DRAFT — NOT APPROVED FOR DISTRIBUTION**", ""]

    lines += [
        f"*Easy Skill Australia · {report['quarter']} · "
        f"{report['signalsAnalysed']} signals analysed*",
        "",
    ]
    if report["status"] == STATUS_APPROVED and report["approvedBy"]:
        lines += [f"*Approved by {report['approvedBy']} on "
                  f"{(report['approvedAt'] or '')[:10]}*", ""]
    lines.append("---")

    for s in report["sections"]:
        lines += ["", f"## {s['heading']}", ""]
        lines.append(s["body"].strip() or "_This section has not been written._")

    provenance = ("Figures are counted from collected signals; the wording was "
                  "drafted by a language model and reviewed by a person."
                  if report.get("proseSource") == "gemini"
                  else "Figures and wording are both computed directly from collected signals.")
    lines += ["", "---", "",
              f"_Generated by MIOS. {provenance} No figure is estimated._"]
    return "\n".join(lines) + "\n"
