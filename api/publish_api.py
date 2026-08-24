"""HTTP surface for Mode Publish — quarterly reports and their review.

The flow:

    1. POST /api/publish/reports              generate a draft for a quarter
    2. PATCH /api/publish/sections/{id}       edit any section
    3. POST /api/publish/sections/{id}/approve   sign off, one at a time
    4. POST /api/publish/reports/{id}/approve    sign off the whole thing
    5. GET  /api/publish/reports/{id}/export     take it away as Markdown or HTML

**There is deliberately no step 6.** §8.3 of the project spec requires that no
automated action publishes externally available content, so this module has no
endpoint that emails, posts to HubSpot, or distributes in any other way. Export
hands the document to a person; what they do with it is outside MIOS. Adding a
send endpoint here would break that guarantee, not extend it.

Every endpoint requires a signed-in account, and the signed-in account's email
is what gets recorded as the approver — the reviewer cannot be typed in.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from api.auth import require_user
from publish.report import current_quarter, quarter_bounds
from publish.store import (
    ReportError,
    approve_report,
    create_report,
    delete_report,
    get_report,
    list_reports,
    render_markdown,
    set_section_approval,
    update_section,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/publish", tags=["publish"])

#: The report body is a handful of paragraphs. A cap keeps a runaway paste from
#: becoming a row nobody can load.
MAX_SECTION_CHARS = 20_000


def _found(report: dict[str, Any] | None) -> dict[str, Any]:
    if report is None:
        raise HTTPException(status_code=404, detail="No such report.")
    return report


@router.get("/reports")
def reports(
    limit: int = Query(50, ge=1, le=200),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    return {"reports": list_reports(limit=limit), "currentQuarter": current_quarter()}


@router.post("/reports", status_code=201)
def generate_report(
    payload: dict[str, Any] = Body(default_factory=dict),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """Assemble a fresh draft from the signals in one quarter.

    Figures are always counted from the signals. Gemini then rewrites the
    wording — one call for the whole report, against the same daily quota the
    classifier uses. If that quota is gone the computed wording ships instead
    and `proseNote` explains why; the report is never blocked on it.

    Never overwrites an existing report for that quarter — someone may be part
    way through editing it. Each call produces a new draft.
    """
    quarter = (payload.get("quarter") or current_quarter()).strip()
    # One Gemini call per report, charged to the same daily budget classification
    # uses. `useLlm: false` skips it and ships the computed wording.
    use_llm = payload.get("useLlm", True) is not False
    try:
        report = create_report(quarter, title=(payload.get("title") or None),
                               use_llm=use_llm)
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("publish: %s generated %s for %s", user["email"], report["id"], quarter)
    return report


@router.get("/reports/{report_id}")
def one_report(
    report_id: str,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    return _found(get_report(report_id))


@router.delete("/reports/{report_id}")
def remove_report(
    report_id: str,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    if not delete_report(report_id):
        raise HTTPException(status_code=404, detail="No such report.")
    log.info("publish: %s deleted %s", user["email"], report_id)
    return {"deleted": report_id}


@router.patch("/sections/{section_id}")
def edit_section(
    section_id: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """Rewrite a section. Editing clears its approval — see publish/store.py."""
    body = payload.get("body")
    if body is None:
        raise HTTPException(status_code=400, detail="A body is required.")
    if len(str(body)) > MAX_SECTION_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"That section is longer than the {MAX_SECTION_CHARS:,} character limit.",
        )
    try:
        report = update_section(section_id, str(body))
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("publish: %s edited section %s", user["email"], section_id)
    return report


@router.post("/sections/{section_id}/approve")
def approve_section(
    section_id: str,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """Sign off one section, as the signed-in user.

    The reviewer is taken from the session rather than the request body: an
    approval record that the client can name is not an approval record.
    """
    try:
        return set_section_approval(section_id, True, user["email"])
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/sections/{section_id}/approve")
def unapprove_section(
    section_id: str,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    try:
        return set_section_approval(section_id, False, user["email"])
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/reports/{report_id}/approve")
def sign_off(
    report_id: str,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """Approve the report as a whole. Refuses while any section is outstanding.

    This is the end of what MIOS does with the document. Distribution to clients
    happens outside the system, performed by a person — there is no endpoint
    here that sends anything.
    """
    try:
        report = approve_report(report_id, user["email"])
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("publish: %s approved %s", user["email"], report_id)
    return report


@router.get("/reports/{report_id}/export", response_class=PlainTextResponse)
def export_report(
    report_id: str,
    format: str = Query("md", pattern="^(md|html)$"),
    user: dict[str, Any] = Depends(require_user),
) -> PlainTextResponse:
    """The report as a document to take away.

    A draft is exported with an unmissable banner rather than being blocked:
    circulating a draft internally for comment is normal, and refusing the
    download would only push people to copy and paste it without the warning.
    """
    report = _found(get_report(report_id))
    markdown = render_markdown(report)
    log.info("publish: %s exported %s as %s", user["email"], report_id, format)

    if format == "md":
        return PlainTextResponse(
            markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="{report["quarter"]}-market-report.md"'},
        )
    return PlainTextResponse(
        _to_html(report, markdown),
        media_type="text/html; charset=utf-8",
    )


def _to_html(report: dict[str, Any], markdown: str) -> str:
    """A printable page. Deliberately self-contained: no stylesheet to fetch and
    nothing to break when the file is emailed on or opened offline."""
    from html import escape

    draft_banner = ""
    if report["status"] != "approved":
        draft_banner = (
            '<p class="draft">DRAFT — NOT APPROVED FOR DISTRIBUTION</p>'
        )

    body = []
    for s in report["sections"]:
        text = s["body"].strip() or "This section has not been written."
        paragraphs = "".join(
            f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in text.split("\n\n") if p.strip()
        )
        body.append(f"<h2>{escape(s['heading'])}</h2>{paragraphs}")

    approved = ""
    if report["status"] == "approved" and report["approvedBy"]:
        approved = (f'<p class="meta">Approved by {escape(report["approvedBy"])} '
                    f'on {escape((report["approvedAt"] or "")[:10])}</p>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{escape(report['title'])}</title>
<style>
  body {{ font-family: Georgia, serif; max-width: 46em; margin: 3em auto; padding: 0 1.5em;
         line-height: 1.6; color: #1A2837; }}
  h1 {{ font-size: 1.9em; margin-bottom: .2em; }}
  h2 {{ font-size: 1.25em; margin-top: 2em; border-top: 1px solid #ddd; padding-top: .8em; }}
  .meta {{ color: #656E7C; font-size: .9em; }}
  .draft {{ background: #FDF3D3; border: 1px solid #6B4F00; color: #6B4F00;
            padding: .6em 1em; font-weight: bold; letter-spacing: .05em; }}
  footer {{ margin-top: 3em; border-top: 1px solid #ddd; padding-top: 1em;
            color: #656E7C; font-size: .85em; }}
  @media print {{ body {{ margin: 0; }} }}
</style></head><body>
{draft_banner}
<h1>{escape(report['title'])}</h1>
<p class="meta">Easy Skill Australia · {escape(report['quarter'])} ·
{report['signalsAnalysed']} signals analysed</p>
{approved}
{''.join(body)}
<footer>Generated by MIOS. Figures are counts of activity detected in the sources
monitored; none are estimated.</footer>
</body></html>"""


@router.get("/quarters")
def quarters(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Quarters that actually hold signals, so the generate form offers real
    choices rather than an empty date picker."""
    from loader.db import connect

    try:
        with connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT substr(captured_at, 1, 7) AS ym FROM signals "
                "WHERE classified_at IS NOT NULL ORDER BY ym DESC"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("publish: could not list quarters (%s)", exc)
        rows = []

    found: list[str] = []
    for r in rows:
        ym = str(r["ym"] or "")
        if len(ym) == 7:
            q = f"{ym[:4]}-Q{(int(ym[5:7]) - 1) // 3 + 1}"
            if q not in found:
                found.append(q)

    current = current_quarter()
    if current not in found:
        found.insert(0, current)
    return {"quarters": found, "currentQuarter": current}


#: Re-exported so a caller can validate a quarter label without importing the
#: report module directly.
__all__ = ["router", "quarter_bounds"]
