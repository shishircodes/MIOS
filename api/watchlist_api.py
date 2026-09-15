from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from api.auth import require_user
from loader.db import connect

router = APIRouter(prefix="/api", tags=["watchlist"])

_ORDER = """
            ORDER BY
                CASE tier
                    WHEN 'A' THEN 1
                    WHEN 'B' THEN 2
                    WHEN 'C' THEN 3
                    ELSE 4
                END,
                company_name
"""


@router.get("/watchlist")
def get_watchlist(
    user: dict[str, Any] = Depends(require_user),
) -> dict:
    with connect() as conn:
        try:
            rows = conn.execute(
                "SELECT company_name, tier, sector, notes, aliases, source, synced_at "
                "FROM watchlist" + _ORDER
            ).fetchall()
        except Exception:  # noqa: BLE001 - source columns not migrated on this database yet
            rows = conn.execute(
                "SELECT company_name, tier, sector, notes, aliases FROM watchlist" + _ORDER
            ).fetchall()

        companies = []
        last_synced: str | None = None

        for row in rows:
            record = dict(row)
            aliases_raw = record.get("aliases")

            if aliases_raw:
                try:
                    aliases = json.loads(aliases_raw)
                except (json.JSONDecodeError, TypeError):
                    aliases = []
            else:
                aliases = []

            synced_at = record.get("synced_at")
            if synced_at and (last_synced is None or synced_at > last_synced):
                last_synced = synced_at

            companies.append(
                {
                    "company_name": record["company_name"],
                    "tier": record["tier"],
                    "sector": record["sector"],
                    "notes": record["notes"],
                    "aliases": aliases,
                    #: 'hubspot' when the client's CRM supplied it, else the built-in list.
                    "source": record.get("source") or "seed",
                }
            )

        return {
            "total": len(companies),
            "companies": companies,
            "fromHubspot": sum(1 for c in companies if c["source"] == "hubspot"),
            "lastSyncedAt": last_synced,
        }
