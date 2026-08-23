"""Tests for the Watchlist HTTP API."""

from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from config.settings import settings as real_settings
from loader.ingest import init_db


@pytest.fixture
def watchlist_file(tmp_path):
    """A small real watchlist seed, deliberately not ordered by tier."""
    path = tmp_path / "watchlist.json"

    path.write_text(
        json.dumps(
            [
                {
                    "company_name": "Safran",
                    "tier": "C",
                    "sector": "defence",
                    "notes": "Aerospace and defence systems.",
                    "aliases": [
                        "Safran Group",
                        "Safran SA",
                    ],
                },
                {
                    "company_name": "Vale",
                    "tier": "B",
                    "sector": "mining",
                    "notes": "Iron ore and nickel producer.",
                    "aliases": [
                        "Vale S.A.",
                        "Vale Australia",
                    ],
                },
                {
                    "company_name": "Rio Tinto",
                    "tier": "A",
                    "sector": "mining",
                    "notes": "Iron ore, aluminium and copper.",
                    "aliases": [
                        "Rio Tinto Group",
                        "RTIO",
                    ],
                },
                {
                    "company_name": "BHP",
                    "tier": "A",
                    "sector": "mining",
                    "notes": "Major resources company.",
                    "aliases": [
                        "BHP Group",
                        "BHP Billiton",
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )

    return path


@pytest.fixture
def db(tmp_path, watchlist_file, monkeypatch):
    """Temporary SQLite DB used by the Watchlist API."""
    path = tmp_path / "watchlist.db"

    init_db(
        path,
        watchlist_path=watchlist_file,
    )

    patched = dataclasses.replace(
        real_settings,
        db_path=path,
        database_url=None,
    )

    monkeypatch.setattr(
        "loader.db.settings",
        patched,
    )

    return path


@pytest.fixture
def client(db, monkeypatch):
    """Authenticated development client."""
    monkeypatch.setattr(
        "api.auth.settings",
        dataclasses.replace(
            real_settings,
            auth_disabled=True,
        ),
    )

    from api.server import app

    return TestClient(app)


@pytest.fixture
def anon(db, monkeypatch):
    """Anonymous client — protected endpoints must reject it."""
    monkeypatch.setattr(
        "api.auth.settings",
        dataclasses.replace(
            real_settings,
            auth_disabled=False,
        ),
    )

    from api.server import app

    return TestClient(app)


def test_watchlist_requires_sign_in(anon):
    response = anon.get("/api/watchlist")

    assert response.status_code == 401


def test_watchlist_returns_seeded_companies(client):
    response = client.get("/api/watchlist")

    assert response.status_code == 200

    body = response.json()

    assert body["total"] == 4
    assert len(body["companies"]) == 4

    names = {
        company["company_name"]
        for company in body["companies"]
    }

    assert names == {
        "BHP",
        "Rio Tinto",
        "Vale",
        "Safran",
    }


def test_watchlist_is_sorted_by_tier_then_company(client):
    response = client.get("/api/watchlist")

    assert response.status_code == 200

    companies = response.json()["companies"]

    assert [
        (company["tier"], company["company_name"])
        for company in companies
    ] == [
        ("A", "BHP"),
        ("A", "Rio Tinto"),
        ("B", "Vale"),
        ("C", "Safran"),
    ]


def test_watchlist_returns_aliases_as_arrays(client):
    response = client.get("/api/watchlist")

    assert response.status_code == 200

    companies = response.json()["companies"]

    bhp = next(
        company
        for company in companies
        if company["company_name"] == "BHP"
    )

    assert bhp["aliases"] == [
        "BHP Group",
        "BHP Billiton",
    ]


def test_watchlist_returns_company_details(client):
    response = client.get("/api/watchlist")

    assert response.status_code == 200

    companies = response.json()["companies"]

    vale = next(
        company
        for company in companies
        if company["company_name"] == "Vale"
    )

    assert vale["tier"] == "B"
    assert vale["sector"] == "mining"
    assert vale["notes"] == "Iron ore and nickel producer."