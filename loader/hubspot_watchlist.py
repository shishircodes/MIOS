"""Populate the watchlist from the client's HubSpot companies.

The watchlist is what every mode leans on: classification tags a signal with a
client's tier, the digest ranks by it, Mode Push scores the relationship from
it, and the dashboard counts how much of it appeared. Until now it was
`config/watchlist.json`, typed by hand and correct only as of the day somebody
last edited it. The client's own CRM already records which companies matter and
how much, so this reads it from there.

**Read-only against HubSpot.** Two requests: the company property definitions
(`crm.schemas.companies.read`), to offer the tier field's options, and a search
for companies that have a tier (`crm.objects.companies.read`). Nothing is ever
written back.

**Which field is "tier" is a setting, not an assumption.** HubSpot's built-in
field is "Ideal Customer Profile Tier" (`hs_ideal_customer_profile`, values
`tier_1`–`tier_3`), and that is the default. A client that tracks tiers in a
field of their own picks it in the panel and maps its values to A, B and C.
Values mapped to nothing are left off the watchlist.

**Once synced, HubSpot is the list.** A company whose tier is removed in
HubSpot comes off the watchlist on the next sync, and the hand-kept JSON seed
stops being applied (see `loader.ingest._seed_watchlist`), or every pipeline
run would quietly put back companies the client had dropped.

**What the CRM cannot know is kept.** HubSpot has no aliases, and aliases are
how "BHP Group Limited" in an advert matches "BHP". A HubSpot company is matched
to an existing watchlist row with the same matcher classification uses; when it
matches, the existing canonical name and aliases are kept and the HubSpot name
is added as one more alias. Hand-written notes on those rows are kept too.

**A sync that would empty the watchlist is refused.** A wrong field choice maps
every company to nothing, and applying that would strip every client's tier
from every signal. The preview shows it instead.

The service key is stored exactly like a model API key — encrypted in
`llm_credentials` under the name "hubspot" — or read from HUBSPOT_SERVICE_KEY.
It is never logged or returned.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loader.db import connect

log = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"
KEY_NAME = "hubspot"
KEY_ENV = "HUBSPOT_SERVICE_KEY"

MAPPING_KEY = "hubspot:watchlist:mapping"
LAST_SYNC_KEY = "hubspot:watchlist:last_sync"

TIERS = ("A", "B", "C")

DEFAULT_MAPPING: dict[str, Any] = {
    "tierProperty": "hs_ideal_customer_profile",
    "tierMap": {"tier_1": "A", "tier_2": "B", "tier_3": "C"},
    "industryProperty": "industry",
    #: Sync before each pipeline run, once a first sync has been done by hand.
    "autoSync": True,
}

#: HubSpot's search stops paging at 10,000 results.
MAX_RESULTS = 10_000
PAGE_SIZE = 100
#: The search API allows five requests a second per account.
PAGE_PAUSE_SECONDS = 0.25
REQUEST_TIMEOUT_SECONDS = 20

#: Industry to sector, matched on words in the option's value and its label so
#: it works whichever form a portal's industry field uses. Order matters: an
#: "Oil & Energy" industry is oil and gas, not energy transition.
SECTOR_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("oil_gas", ("oil", "gas", "petroleum", "lng")),
    ("energy_transition", ("renewable", "solar", "wind", "hydrogen", "clean energy")),
    ("mining", ("mining", "metal", "mineral")),
    ("defence", ("defense", "defence", "military", "aerospace", "aviation")),
    ("construction", ("construction", "civil engineering", "building", "infrastructure")),
)

_TIER_RANK = {"A": 0, "B": 1, "C": 2}


class HubSpotError(RuntimeError):
    """HubSpot could not be read. The message is shown to an administrator."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class HubSpotNotConfigured(HubSpotError):
    """No service key has been entered or set in the environment."""


# --------------------------------------------------------------------------
# Key and settings
# --------------------------------------------------------------------------


def api_key(target: str | Path | None = None) -> str:
    """The key in play: one entered in the panel, else HUBSPOT_SERVICE_KEY."""
    from loader.credentials import key_for

    return key_for(KEY_NAME, os.environ.get(KEY_ENV, "").strip(), target)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _kv_get(key: str, target: str | Path | None) -> Any:
    try:
        with connect(target, readonly=True) as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        log.debug("hubspot: could not read %s (%s)", key, exc)
        return None
    if not row or not row["value"]:
        return None
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return None


def _kv_set(key: str, value: Any, target: str | Path | None) -> None:
    body = json.dumps(value)
    with connect(target) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO kv_store (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = ?",
            (key, body, body),
        )


def get_mapping(target: str | Path | None = None) -> dict[str, Any]:
    stored = _kv_get(MAPPING_KEY, target) or {}
    return {**DEFAULT_MAPPING, **stored}


def set_mapping(mapping: dict[str, Any], *, changed_by: str,
                target: str | Path | None = None) -> dict[str, Any]:
    """Validate and store which HubSpot fields feed the watchlist."""
    tier_property = str(mapping.get("tierProperty") or "").strip()
    if not tier_property:
        raise ValueError("Choose the HubSpot field that holds each company's tier.")
    raw_map = mapping.get("tierMap") or {}
    if not isinstance(raw_map, dict):
        raise ValueError("The tier mapping must pair each HubSpot value with A, B, C or nothing.")
    tier_map = {str(k): str(v).upper() for k, v in raw_map.items() if v}
    bad = sorted({v for v in tier_map.values() if v not in TIERS})
    if bad:
        raise ValueError(f"Tiers must be A, B or C; got {', '.join(bad)}.")
    if not tier_map:
        raise ValueError("Map at least one HubSpot value to a tier, or no company can be synced.")

    stored = {
        "tierProperty": tier_property,
        "tierMap": tier_map,
        "industryProperty": str(mapping.get("industryProperty") or "").strip() or None,
        "autoSync": bool(mapping.get("autoSync", True)),
        "changedBy": changed_by,
        "changedAt": _now(),
    }
    _kv_set(MAPPING_KEY, stored, target)
    log.info("hubspot: %s set the watchlist mapping (tier field %s, %d values)",
             changed_by, tier_property, len(tier_map))
    return get_mapping(target)


def last_sync(target: str | Path | None = None) -> dict[str, Any] | None:
    return _kv_get(LAST_SYNC_KEY, target)


# --------------------------------------------------------------------------
# HubSpot
# --------------------------------------------------------------------------


class HubSpotClient:
    """The two read-only calls this feature makes."""

    def __init__(self, key: str, *, session: Any = None, base_url: str = BASE_URL,
                 pause: float = PAGE_PAUSE_SECONDS) -> None:
        if not key:
            raise HubSpotNotConfigured(
                "No HubSpot service key: none entered in the panel, and "
                f"{KEY_ENV} is not set either.")
        if session is None:
            import requests

            session = requests.Session()
        self._key = key
        self._session = session
        self._base = base_url.rstrip("/")
        self._pause = pause

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        for attempt in range(3):
            try:
                response = self._session.request(
                    method, self._base + path, headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
            except Exception as exc:  # noqa: BLE001 - network failure, reported plainly
                raise HubSpotError(f"Could not reach HubSpot ({type(exc).__name__}).") from exc

            status = response.status_code
            if status == 429 and attempt < 2:
                time.sleep(float(response.headers.get("Retry-After") or 1.0))
                continue
            if status < 400:
                return response.json()

            detail = ""
            try:
                detail = str((response.json() or {}).get("message") or "")
            except ValueError:
                pass
            if status == 401:
                raise HubSpotError(
                    "HubSpot refused the service key. It may be mistyped, rotated or "
                    "deleted — check it under Settings → Integrations → Service Keys.", 401)
            if status == 403:
                raise HubSpotError(
                    "The service key is missing a permission this needs. It requires "
                    "crm.objects.companies.read and crm.schemas.companies.read."
                    + (f" HubSpot said: {detail}" if detail else ""), 403)
            raise HubSpotError(f"HubSpot returned an error ({status})."
                               + (f" {detail}" if detail else ""), status)
        raise HubSpotError("HubSpot kept asking us to slow down. Try again in a minute.", 429)

    def company_properties(self) -> list[dict[str, Any]]:
        """Company fields, for choosing which one holds the tier."""
        body = self._request("GET", "/crm/v3/properties/companies")
        out = []
        for p in body.get("results") or []:
            if p.get("archived") or p.get("hidden"):
                continue
            out.append({
                "name": p.get("name"),
                "label": p.get("label") or p.get("name"),
                "type": p.get("type"),
                "options": [
                    {"value": o.get("value"), "label": o.get("label") or o.get("value")}
                    for o in (p.get("options") or []) if not o.get("hidden")
                ],
            })
        return sorted(out, key=lambda p: str(p["label"]).lower())

    def search_companies(self, properties: list[str], tier_property: str) -> tuple[list[dict[str, Any]], bool]:
        """Every company with a value in the tier field. Returns (companies, truncated)."""
        results: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            body: dict[str, Any] = {
                "filterGroups": [{"filters": [
                    {"propertyName": tier_property, "operator": "HAS_PROPERTY"}]}],
                "properties": properties,
                "limit": PAGE_SIZE,
                # A stable order, so paging cannot skip or repeat a company.
                "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
            }
            if after:
                body["after"] = after
            page = self._request("POST", "/crm/v3/objects/companies/search", json=body)
            results.extend(page.get("results") or [])
            after = ((page.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return results, False
            if len(results) >= MAX_RESULTS:
                return results, True
            time.sleep(self._pause)


# --------------------------------------------------------------------------
# Planning — pure, so the rules can be tested without HubSpot or a database
# --------------------------------------------------------------------------


def sector_for(value: str | None, label: str | None = None) -> str | None:
    """The watchlist sector an industry implies, or None when it implies none."""
    text = f"{value or ''} {label or ''}".lower().replace("_", " ")
    if not text.strip():
        return None
    for sector, words in SECTOR_KEYWORDS:
        if any(w in text for w in words):
            return sector
    return "other"


def _aliases(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(a) for a in raw]
    try:
        return [str(a) for a in json.loads(raw or "[]")]
    except (TypeError, ValueError):
        return []


def plan(companies: list[dict[str, Any]], existing: list[dict[str, Any]],
         mapping: dict[str, Any], industry_labels: dict[str, str] | None = None) -> dict[str, Any]:
    """What a sync would do, without doing it."""
    from agents.signal_analyst import fuzzy_match_watchlist

    industry_labels = industry_labels or {}
    tier_property = mapping["tierProperty"]
    tier_map: dict[str, str] = mapping.get("tierMap") or {}
    industry_property = mapping.get("industryProperty")

    rows_by_name = {r["company_name"]: {**r, "aliases": _aliases(r.get("aliases"))} for r in existing}
    by_external = {str(r["external_id"]): r["company_name"]
                   for r in rows_by_name.values() if r.get("external_id")}
    matchable = [{"company_name": r["company_name"], "tier": r["tier"], "sector": r.get("sector"),
                  "aliases": r["aliases"], "all_names": [r["company_name"], *r["aliases"]]}
                 for r in rows_by_name.values()]

    chosen: dict[str, dict[str, Any]] = {}
    skipped_unmapped: Counter[str] = Counter()
    skipped_no_name = 0

    for company in companies:
        props = company.get("properties") or {}
        name = str(props.get("name") or "").strip()
        if not name:
            skipped_no_name += 1
            continue
        raw_tier = str(props.get(tier_property) or "").strip()
        tier = tier_map.get(raw_tier)
        if tier not in TIERS:
            skipped_unmapped[raw_tier or "(blank)"] += 1
            continue

        external_id = str(company.get("id") or props.get("hs_object_id") or "")
        canonical = by_external.get(external_id)
        if canonical is None:
            canonical, _ = fuzzy_match_watchlist(name, matchable)
        base = rows_by_name.get(canonical) if canonical else None
        canonical = canonical or name

        industry_value = str(props.get(industry_property) or "") if industry_property else ""
        sector = (sector_for(industry_value, industry_labels.get(industry_value))
                  or (base or {}).get("sector") or "other")

        aliases = list((base or {}).get("aliases") or [])
        if name != canonical and name not in aliases:
            aliases.append(name)

        if base and base.get("source") != "hubspot" and base.get("notes"):
            notes = base["notes"]
        else:
            bits = [industry_labels.get(industry_value) or industry_value or None, props.get("domain")]
            notes = "From HubSpot" + (": " + " · ".join(b for b in bits if b) if any(bits) else "")

        row = {"company_name": canonical, "tier": tier, "sector": sector, "notes": notes,
               "aliases": aliases, "external_id": external_id, "hubspotName": name}

        # Two HubSpot records for one client keep the stronger tier.
        if canonical in chosen:
            prior = chosen[canonical]
            merged = sorted(set(prior["aliases"]) | set(aliases))
            if _TIER_RANK[tier] < _TIER_RANK[prior["tier"]]:
                row["aliases"] = merged
                chosen[canonical] = row
            else:
                prior["aliases"] = merged
            continue
        chosen[canonical] = row

    added, updated, unchanged = [], [], []
    for row in chosen.values():
        before = rows_by_name.get(row["company_name"])
        if before is None:
            added.append(row)
            continue
        row["previousTier"] = before["tier"]
        same = (before["tier"] == row["tier"] and before.get("sector") == row["sector"]
                and sorted(before["aliases"]) == sorted(row["aliases"])
                and before.get("source") == "hubspot")
        (unchanged if same else updated).append(row)

    removed = [{"company_name": r["company_name"], "tier": r["tier"], "source": r.get("source")}
               for name, r in rows_by_name.items() if name not in chosen]

    return {
        "rows": list(chosen.values()),
        "added": added, "updated": updated, "unchanged": unchanged, "removed": removed,
        "tiers": dict(Counter(r["tier"] for r in chosen.values())),
        "skippedUnmapped": dict(skipped_unmapped),
        "skippedNoName": skipped_no_name,
    }


# --------------------------------------------------------------------------
# Syncing
# --------------------------------------------------------------------------


def _existing_rows(target: str | Path | None) -> list[dict[str, Any]]:
    with connect(target, readonly=True) as conn:
        try:
            rows = conn.execute(
                "SELECT company_name, tier, sector, notes, aliases, source, external_id "
                "FROM watchlist").fetchall()
        except Exception:  # noqa: BLE001 - columns not migrated yet on this database
            rows = conn.execute(
                "SELECT company_name, tier, sector, notes, aliases FROM watchlist").fetchall()
    return [dict(r) for r in rows]


def _preview(rows: list[dict[str, Any]], limit: int = 50) -> list[dict[str, Any]]:
    keep = ("company_name", "tier", "previousTier", "sector", "hubspotName", "source")
    return [{k: r.get(k) for k in keep if k in r} for r in rows[:limit]]


def sync(target: str | Path | None = None, *, changed_by: str, dry_run: bool = False,
         client: HubSpotClient | None = None) -> dict[str, Any]:
    """Read HubSpot and bring the watchlist into line with it.

    With `dry_run` nothing is written. Otherwise the watchlist is replaced in one
    transaction and existing signals are re-tagged against it.
    """
    mapping = get_mapping(target)
    client = client or HubSpotClient(api_key(target))

    industry_labels: dict[str, str] = {}
    if mapping.get("industryProperty"):
        try:
            for prop in client.company_properties():
                if prop["name"] == mapping["industryProperty"]:
                    industry_labels = {o["value"]: o["label"] for o in prop["options"]}
                    break
        except HubSpotError as exc:
            log.warning("hubspot: could not read industry labels (%s) — using raw values", exc)

    wanted = [p for p in dict.fromkeys(
        ["name", "domain", mapping["tierProperty"], mapping.get("industryProperty")]) if p]
    companies, truncated = client.search_companies(wanted, mapping["tierProperty"])
    result = plan(companies, _existing_rows(target), mapping, industry_labels)

    summary: dict[str, Any] = {
        "at": _now(),
        "by": changed_by,
        "dryRun": dry_run,
        "fetched": len(companies),
        "truncated": truncated,
        "total": len(result["rows"]),
        "tiers": result["tiers"],
        "added": len(result["added"]),
        "updated": len(result["updated"]),
        "unchanged": len(result["unchanged"]),
        "removed": len(result["removed"]),
        "skippedUnmapped": result["skippedUnmapped"],
        "skippedNoName": result["skippedNoName"],
        "tierProperty": mapping["tierProperty"],
        "preview": {
            "added": _preview(result["added"]),
            "updated": _preview(result["updated"]),
            "removed": _preview(result["removed"]),
        },
    }

    if not result["rows"]:
        summary["refused"] = (
            f"No HubSpot company has a value in “{mapping['tierProperty']}” that maps to "
            "A, B or C, so syncing would empty the watchlist. Nothing was changed — check "
            "the tier field and its mapping.")
        if not dry_run:
            raise HubSpotError(summary["refused"])
        return summary

    if dry_run:
        return summary

    from loader.ingest import _apply_column_additions

    stamp = _now()
    with connect(target) as conn:
        _apply_column_additions(conn)
        conn.executemany(
            "INSERT INTO watchlist (company_name, tier, sector, notes, aliases, source, "
            "external_id, synced_at) VALUES (?, ?, ?, ?, ?, 'hubspot', ?, ?) "
            "ON CONFLICT (company_name) DO UPDATE SET "
            "tier = EXCLUDED.tier, sector = EXCLUDED.sector, notes = EXCLUDED.notes, "
            "aliases = EXCLUDED.aliases, source = EXCLUDED.source, "
            "external_id = EXCLUDED.external_id, synced_at = EXCLUDED.synced_at",
            [(r["company_name"], r["tier"], r["sector"], r["notes"], json.dumps(r["aliases"]),
              r["external_id"], stamp) for r in result["rows"]],
        )
        for gone in result["removed"]:
            conn.execute("DELETE FROM watchlist WHERE company_name = ?", (gone["company_name"],))

    # Signals already collected carry tiers from the old list. Re-derived from
    # names alone — no model calls.
    try:
        from loader.rematch import rematch

        summary["retagged"] = rematch(target)
    except Exception as exc:  # noqa: BLE001 - the watchlist itself is already updated
        log.warning("hubspot: watchlist synced but signals were not re-tagged (%s)", exc)
        summary["retagged"] = None

    _kv_set(LAST_SYNC_KEY, {k: v for k, v in summary.items() if k != "preview"}, target)
    log.info("hubspot: %s synced the watchlist — %d companies (+%d, ~%d, -%d)",
             changed_by, summary["total"], summary["added"], summary["updated"], summary["removed"])
    return summary


def auto_sync(target: str | Path | None = None) -> dict[str, Any] | None:
    """Sync before a pipeline run, when an administrator has set it up.

    Only after a first sync done by hand — so a key entered but not yet checked
    never rewrites the watchlist unattended — and never fatal: a HubSpot outage
    leaves the run on the watchlist it already has.
    """
    mapping = get_mapping(target)
    if not mapping.get("autoSync") or not last_sync(target) or not api_key(target):
        return None
    try:
        return sync(target, changed_by="scheduled run")
    except HubSpotError as exc:
        log.warning("hubspot: pre-run sync skipped — %s", exc)
        return {"error": str(exc)}


def status(target: str | Path | None = None) -> dict[str, Any]:
    """Everything the admin panel shows, without the key and without calling HubSpot."""
    from loader.credentials import available, describe

    key = describe(KEY_NAME, os.environ.get(KEY_ENV, "").strip(), target=target)
    counts: dict[str, int] = {}
    try:
        with connect(target, readonly=True) as conn:
            try:
                for r in conn.execute(
                        "SELECT COALESCE(source, 'seed') AS source, count(*) AS n "
                        "FROM watchlist GROUP BY COALESCE(source, 'seed')").fetchall():
                    counts[str(r["source"])] = int(r["n"] or 0)
            except Exception:  # noqa: BLE001 - source column not migrated yet
                # No column means nothing has ever been synced, so every row is
                # the built-in list. Reporting zero here read as an empty watchlist.
                counts["seed"] = int(conn.execute("SELECT count(*) FROM watchlist").fetchone()[0])
    except Exception as exc:  # noqa: BLE001 - no watchlist table at all
        log.debug("hubspot: could not count the watchlist (%s)", exc)
    return {
        "key": {k: key.get(k) for k in ("source", "hint", "shadowsEnvironment", "unreadable")},
        "canStoreKey": available(),
        "keyEnv": KEY_ENV,
        "mapping": get_mapping(target),
        "lastSync": last_sync(target),
        "watchlist": {"fromHubspot": counts.get("hubspot", 0), "fromSeed": counts.get("seed", 0)},
    }
