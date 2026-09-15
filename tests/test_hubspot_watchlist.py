"""Filling the watchlist from HubSpot.

HubSpot is never called: a fake session stands in for it. What is pinned is the
part that decides who counts as a client — which companies make the list, at
what tier, under which name — and the guards around applying it.
"""
from __future__ import annotations

import json

import pytest

from loader import hubspot_watchlist as hs
from loader.db import connect
from loader.ingest import init_db

MAPPING = dict(hs.DEFAULT_MAPPING)


def company(cid, name, tier="tier_1", industry=None, domain=None):
    props = {"name": name, "hs_ideal_customer_profile": tier, "hs_object_id": cid}
    if industry is not None:
        props["industry"] = industry
    if domain is not None:
        props["domain"] = domain
    return {"id": cid, "properties": props}


def existing(name, tier="A", sector="mining", aliases=(), notes=None, source=None, external_id=None):
    return {"company_name": name, "tier": tier, "sector": sector, "notes": notes,
            "aliases": json.dumps(list(aliases)), "source": source, "external_id": external_id}


# ---------- planning ----------


def test_tiers_map_through_the_chosen_field():
    result = hs.plan([company("1", "Acme Mining", "tier_1"),
                      company("2", "Beta Build", "tier_2"),
                      company("3", "Gamma Gas", "tier_3")], [], MAPPING)
    tiers = {r["company_name"]: r["tier"] for r in result["rows"]}
    assert tiers == {"Acme Mining": "A", "Beta Build": "B", "Gamma Gas": "C"}


def test_a_value_mapped_to_nothing_is_left_off_and_counted():
    mapping = {**MAPPING, "tierMap": {"tier_1": "A"}}
    result = hs.plan([company("1", "Acme", "tier_1"), company("2", "Other", "tier_3")], [], mapping)
    assert [r["company_name"] for r in result["rows"]] == ["Acme"]
    assert result["skippedUnmapped"] == {"tier_3": 1}


def test_an_existing_client_keeps_its_name_and_aliases():
    """HubSpot has no aliases, and aliases are how adverts match a client."""
    rows = [existing("BHP", aliases=["BHP Billiton"], notes="Hand-written context.")]
    result = hs.plan([company("9", "BHP Group Limited", "tier_2")], rows, MAPPING)

    bhp = result["rows"][0]
    assert bhp["company_name"] == "BHP"
    assert bhp["tier"] == "B"
    assert set(bhp["aliases"]) == {"BHP Billiton", "BHP Group Limited"}
    assert bhp["notes"] == "Hand-written context."
    assert [r["company_name"] for r in result["updated"]] == ["BHP"]


def test_a_company_hubspot_no_longer_tiers_comes_off():
    rows = [existing("BHP"), existing("Old Client", tier="C")]
    result = hs.plan([company("1", "BHP")], rows, MAPPING)
    assert [r["company_name"] for r in result["removed"]] == ["Old Client"]


def test_the_hubspot_id_matches_before_the_name_does():
    """A client renamed in HubSpot stays the same watchlist row."""
    rows = [existing("Northwind", source="hubspot", external_id="42")]
    result = hs.plan([company("42", "Northwind Resources Renamed")], rows, MAPPING)
    assert result["rows"][0]["company_name"] == "Northwind"
    assert result["removed"] == []


def test_duplicate_hubspot_records_keep_the_stronger_tier():
    rows = [existing("BHP")]
    result = hs.plan([company("1", "BHP Group", "tier_3"), company("2", "BHP Limited", "tier_1")],
                     rows, MAPPING)
    assert len(result["rows"]) == 1 and result["rows"][0]["tier"] == "A"


@pytest.mark.parametrize("value,label,sector", [
    ("MINING_METALS", "Mining & Metals", "mining"),
    ("OIL_ENERGY", "Oil & Energy", "oil_gas"),
    ("RENEWABLES_ENVIRONMENT", "Renewables & Environment", "energy_transition"),
    ("CONSTRUCTION", "Construction", "construction"),
    ("DEFENSE_SPACE", "Defense & Space", "defence"),
    ("COMPUTER_SOFTWARE", "Computer Software", "other"),
])
def test_industry_becomes_a_sector_whichever_form_it_takes(value, label, sector):
    assert hs.sector_for(value, label) == sector
    assert hs.sector_for(value) == sector


def test_a_nameless_company_is_skipped():
    result = hs.plan([company("1", "  ")], [], MAPPING)
    assert result["rows"] == [] and result["skippedNoName"] == 1


# ---------- mapping ----------


@pytest.fixture
def db(tmp_path, monkeypatch):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([
        {"company_name": "BHP", "tier": "A", "sector": "mining", "notes": "Seed note.",
         "aliases": ["BHP Billiton"]},
        {"company_name": "Dropped Co", "tier": "C", "sector": "other", "notes": "", "aliases": []},
    ]))
    path = tmp_path / "hs.db"
    init_db(path, watchlist_path=wl)
    monkeypatch.setattr("loader.hubspot_watchlist.connect", lambda t=None, **kw: connect(path, **kw))
    monkeypatch.setattr("loader.rematch.connect", lambda t=None, **kw: connect(path, **kw))
    monkeypatch.setattr("loader.credentials.connect", lambda t=None, **kw: connect(path, **kw))
    return path, wl


def test_a_mapping_to_an_unknown_tier_is_refused(db):
    with pytest.raises(ValueError, match="A, B or C"):
        hs.set_mapping({"tierProperty": "tier", "tierMap": {"gold": "Z"}}, changed_by="x")


def test_a_mapping_with_nothing_mapped_is_refused(db):
    with pytest.raises(ValueError, match="at least one"):
        hs.set_mapping({"tierProperty": "tier", "tierMap": {"gold": ""}}, changed_by="x")


# ---------- the HubSpot client ----------


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_search_pages_until_hubspot_stops_and_sends_the_key_as_bearer():
    session = FakeSession([
        FakeResponse(200, {"results": [company("1", "A")], "paging": {"next": {"after": "1"}}}),
        FakeResponse(200, {"results": [company("2", "B")]}),
    ])
    client = hs.HubSpotClient("pat-secret", session=session, pause=0)

    found, truncated = client.search_companies(["name"], "hs_ideal_customer_profile")

    assert [c["id"] for c in found] == ["1", "2"] and truncated is False
    method, url, kwargs = session.calls[1]
    assert (method, url.endswith("/crm/v3/objects/companies/search")) == ("POST", True)
    assert kwargs["headers"]["Authorization"] == "Bearer pat-secret"
    assert kwargs["json"]["after"] == "1"
    assert kwargs["json"]["filterGroups"][0]["filters"][0]["operator"] == "HAS_PROPERTY"


@pytest.mark.parametrize("status,words", [(401, "refused the service key"), (403, "permission")])
def test_auth_failures_say_what_to_fix(status, words):
    client = hs.HubSpotClient("k", session=FakeSession([FakeResponse(status, {})]), pause=0)
    with pytest.raises(hs.HubSpotError, match=words):
        client.company_properties()


def test_no_key_is_reported_as_not_configured():
    with pytest.raises(hs.HubSpotNotConfigured):
        hs.HubSpotClient("")


# ---------- applying a sync ----------


class FakeClient:
    def __init__(self, companies):
        self.companies = companies

    def company_properties(self):
        return [{"name": "industry", "label": "Industry", "type": "enumeration",
                 "options": [{"value": "MINING_METALS", "label": "Mining & Metals"}]}]

    def search_companies(self, properties, tier_property):
        return self.companies, False


def _watchlist(path):
    with connect(path) as conn:
        return {r["company_name"]: dict(r) for r in conn.execute("SELECT * FROM watchlist").fetchall()}


def test_a_preview_changes_nothing(db):
    path, _ = db
    before = _watchlist(path)
    result = hs.sync(changed_by="boss", dry_run=True,
                     client=FakeClient([company("1", "BHP Group", "tier_2")]))
    assert result["removed"] == 1 and result["updated"] == 1
    assert _watchlist(path) == before
    assert hs.last_sync() is None


def test_applying_replaces_the_list_and_marks_its_source(db):
    path, _ = db
    result = hs.sync(changed_by="boss", client=FakeClient([
        company("1", "BHP Group", "tier_2", industry="MINING_METALS"),
        company("2", "New Client Pty Ltd", "tier_1", industry="MINING_METALS", domain="new.example"),
    ]))

    rows = _watchlist(path)
    assert set(rows) == {"BHP", "New Client Pty Ltd"}
    assert rows["BHP"]["tier"] == "B" and rows["BHP"]["source"] == "hubspot"
    assert rows["New Client Pty Ltd"]["sector"] == "mining"
    assert "Mining & Metals" in rows["New Client Pty Ltd"]["notes"]
    assert result["retagged"] is not None
    assert hs.last_sync()["total"] == 2


def test_a_sync_that_would_empty_the_watchlist_is_refused(db):
    path, _ = db
    before = _watchlist(path)
    with pytest.raises(hs.HubSpotError, match="empty the watchlist"):
        hs.sync(changed_by="boss", client=FakeClient([company("1", "BHP", "unmapped_value")]))
    assert _watchlist(path) == before


def test_the_seed_no_longer_overwrites_a_synced_watchlist(db):
    """init_db runs on every pipeline run; it must not undo a sync."""
    path, wl = db
    hs.sync(changed_by="boss", client=FakeClient([company("1", "BHP", "tier_3")]))

    init_db(path, watchlist_path=wl)

    rows = _watchlist(path)
    assert set(rows) == {"BHP"}, "the seed must not restore a company HubSpot dropped"
    assert rows["BHP"]["tier"] == "C", "the seed must not reset a HubSpot tier"


def test_auto_sync_waits_for_a_first_sync_by_hand(db, monkeypatch):
    monkeypatch.setenv(hs.KEY_ENV, "pat-from-env")
    assert hs.auto_sync() is None


def test_status_counts_an_unmigrated_watchlist_as_the_built_in_list(tmp_path, monkeypatch):
    """A database from before the source column: every row is the seed. It
    reported zero, which read as an empty watchlist."""
    path = tmp_path / "old.db"
    with connect(path) as conn:
        conn.execute("CREATE TABLE watchlist (company_name TEXT PRIMARY KEY, tier TEXT NOT NULL, "
                     "sector TEXT, notes TEXT, aliases TEXT)")
        conn.execute("INSERT INTO watchlist VALUES ('BHP', 'A', 'mining', '', '[]')")
        conn.execute("INSERT INTO watchlist VALUES ('Rio Tinto', 'A', 'mining', '', '[]')")
    monkeypatch.setattr("loader.hubspot_watchlist.connect", lambda t=None, **kw: connect(path, **kw))
    monkeypatch.setattr("loader.credentials.connect", lambda t=None, **kw: connect(path, **kw))

    assert hs.status()["watchlist"] == {"fromHubspot": 0, "fromSeed": 2}


def test_status_never_contains_the_key(db, monkeypatch):
    monkeypatch.setenv(hs.KEY_ENV, "pat-na1-very-secret-value")
    body = json.dumps(hs.status())
    assert "very-secret" not in body
    assert '"source": "environment"' in body
