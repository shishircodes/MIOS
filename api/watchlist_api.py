from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from api.auth import require_user
from loader.db import connect

router = APIRouter(prefix="/api", tags=["watchlist"])


@router.get("/watchlist")
def get_watchlist(
    user: dict[str, Any] = Depends(require_user),
) -> dict:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT company_name, tier, sector, notes, aliases
            FROM watchlist
            ORDER BY
                CASE tier
                    WHEN 'A' THEN 1
                    WHEN 'B' THEN 2
                    WHEN 'C' THEN 3
                    ELSE 4
                END,
                company_name
            """
        ).fetchall()

        companies = []

        for row in rows:
            aliases_raw = row["aliases"]

            if aliases_raw:
                try:
                    aliases = json.loads(aliases_raw)
                except (json.JSONDecodeError, TypeError):
                    aliases = []
            else:
                aliases = []

            companies.append(
                {
                    "company_name": row["company_name"],
                    "tier": row["tier"],
                    "sector": row["sector"],
                    "notes": row["notes"],
                    "aliases": aliases,
                }
            )

        return {
            "total": len(companies),
            "companies": companies,
        }