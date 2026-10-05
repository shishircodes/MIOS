"""World Bank projects and procurement notices for Papua New Guinea.

Development-bank money is how most large PNG infrastructure gets built, and the
bank publishes both ends of it: the projects it has approved, and each tender
and contract award under them. A road rehabilitation contract awarded to a
named contractor is a hiring signal months before the job adverts.

Two public JSON services, no key:

* **Procurement notices** — invitations to bid and contract awards, newest
  first. These are the events.
* **Projects** — the active portfolio, which changes slowly. Read as well so a
  newly approved project is noticed when it appears; once stored, a project is
  not stored again.

Most of the bank's PNG portfolio is health, education and public-sector reform,
which is not Easy Skill's market. A notice is kept when it is for civil works,
or when its wording is about building, power, transport or resources.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

SOURCE_NAME = "worldbank"
SOURCE_TYPE = "tender"
GEOGRAPHY = "PNG"
COUNTRY_CODE = "PG"

BASE_URL = "https://search.worldbank.org/api/v2"
NOTICE_PAGE = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{id}"

USER_AGENT = "Mozilla/5.0 (compatible; MIOS/0.2; +https://easyskill.com.au)"
REQUEST_TIMEOUT = 25
#: Notices are requested in a larger batch than the limit, because most of a
#: batch is dropped as outside the relevant sectors.
NOTICE_ROWS = 100
MAX_TEXT_CHARS = 500

#: Words that put a notice or project in Easy Skill's sectors.
#:
#: Whole words only. The bank's notices are full of "support", "empowerment"
#: and "capacity building", and an unanchored "port", "power" or "building"
#: matched all three — which let through exactly the health and education
#: consultancies this filter exists to drop. "building" is left out altogether
#: for the same reason; a real one says "construction".
RELEVANT = re.compile(
    r"\b(?:roads?|bridges?|highways?|transport\w*|ports?|wharf\w*|wharves|airports?|"
    r"aviation|rail\w*|energy|power|electri\w+|grid|hydro\w*|solar|transmission|"
    r"substations?|construct\w+|civil\s+works|rehabilitat\w+|infrastructure|"
    r"water\s+supply|sanitation|mining|minerals?|petroleum|gas|engineering|"
    r"supervision\s+of\s+works|design\s+and\s+build)\b",
    re.IGNORECASE,
)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(html: str | None) -> str:
    return _WS.sub(" ", _TAG.sub(" ", html or "")).strip()


def is_relevant(*parts: str | None, group: str | None = None) -> bool:
    """Whether a notice or project belongs to a sector Easy Skill recruits into.

    `group` is the bank's procurement group; "CW" is civil works, which is
    relevant whatever the wording.
    """
    if (group or "").upper() == "CW":
        return True
    return bool(RELEVANT.search(" ".join(p for p in parts if p)))


def parse_notices(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The procurement service's JSON -> signal records. Pure, for tests."""
    out: list[dict[str, Any]] = []
    for n in (payload or {}).get("procnotices") or []:
        notice_id = str(n.get("id") or "").strip()
        description = str(n.get("bid_description") or "").strip()
        project = str(n.get("project_name") or "").strip()
        if not notice_id or not (description or project):
            continue
        if not is_relevant(description, project, group=n.get("procurement_group")):
            continue
        kind = str(n.get("notice_type") or "Procurement notice").strip()
        method = str(n.get("procurement_method_name") or "").strip()
        detail = _clean(n.get("notice_text"))
        if len(detail) > MAX_TEXT_CHARS:
            detail = detail[:MAX_TEXT_CHARS].rsplit(" ", 1)[0] + "…"
        raw_content = " | ".join(p for p in (
            description or project,
            f"World Bank {kind.lower()}, Papua New Guinea",
            f"project: {project}" if project and description else "",
            method,
            detail,
        ) if p)
        out.append({
            "source_url": NOTICE_PAGE.format(id=notice_id),
            "raw_content": raw_content,
            "captured_at": _now_iso(),
            "posted": n.get("noticedate") or None,
            "title": description or project,
            "publication": "World Bank",
            "agency": project,
            "category": kind,
            "source_name": SOURCE_NAME,
            "source_type": SOURCE_TYPE,
            "geography": GEOGRAPHY,
        })
    return out


def parse_projects(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The projects service's JSON -> signal records. Pure, for tests."""
    out: list[dict[str, Any]] = []
    for p in ((payload or {}).get("projects") or {}).values():
        if not isinstance(p, dict):
            continue
        name = str(p.get("project_name") or "").strip()
        url = str(p.get("url") or "").strip()
        if not name or not url:
            continue
        sector = (p.get("sector1") or {}).get("Name") if isinstance(p.get("sector1"), dict) else ""
        agency = str(p.get("impagency") or "").strip()
        if not is_relevant(name, sector, agency):
            continue
        amount = str(p.get("totalamt") or "").strip()
        approved = str(p.get("boardapprovaldate") or "")[:10]
        raw_content = " | ".join(part for part in (
            name,
            f"World Bank project, Papua New Guinea — {str(p.get('status') or '').lower() or 'listed'}",
            f"implemented by {agency}" if agency else "",
            f"US${amount} committed" if amount and amount != "0" else "",
            f"approved {approved}" if approved else "",
            f"sector: {sector}" if sector else "",
        ) if part)
        out.append({
            "source_url": url,
            "raw_content": raw_content,
            "captured_at": _now_iso(),
            "posted": approved or None,
            "title": name,
            "publication": "World Bank",
            "agency": agency,
            "category": "Project",
            "source_name": SOURCE_NAME,
            "source_type": SOURCE_TYPE,
            "geography": GEOGRAPHY,
        })
    return out


def _scrape_sync(limit: int, base_url: str) -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    notices: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    try:
        res = session.get(f"{base_url}/procnotices", timeout=REQUEST_TIMEOUT, params={
            "format": "json", "project_ctry_code_exact": COUNTRY_CODE,
            "rows": NOTICE_ROWS, "srt": "noticedate", "order": "desc",
        })
        res.raise_for_status()
        notices = parse_notices(res.json())
    except Exception as exc:  # noqa: BLE001 - the projects call may still work
        log.warning("%s: procurement notices unreachable (%s)", SOURCE_NAME, exc)
    try:
        res = session.get(f"{base_url}/projects", timeout=REQUEST_TIMEOUT, params={
            "format": "json", "countrycode_exact": COUNTRY_CODE, "status_exact": "Active",
            "rows": 50, "srt": "boardapprovaldate", "order": "desc",
        })
        res.raise_for_status()
        projects = parse_projects(res.json())
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: projects unreachable (%s)", SOURCE_NAME, exc)

    # Notices first: they are this week's events. Projects fill what is left.
    collected = (notices + projects)[:limit]
    log.info("%s: %d relevant notices, %d relevant projects, %d kept",
             SOURCE_NAME, len(notices), len(projects), len(collected))
    return collected


async def scrape_async(limit: int = 50, base_url: str | None = None) -> list[dict[str, Any]]:
    """Never raises; returns [] on any failure, like the other sources."""
    try:
        return await asyncio.to_thread(_scrape_sync, limit, (base_url or BASE_URL).rstrip("/"))
    except Exception as exc:  # noqa: BLE001
        log.error("%s: scrape failed (%s)", SOURCE_NAME, exc)
        return []
