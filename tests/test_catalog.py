"""The source catalogue (scraper.catalog) and what is derived from it.

The catalogue is the one list of every source in the data-sources guide. The
registry, the on/off defaults, the admin page and the names shown on a signal
are all read from it, so the things worth pinning are the joins: every
collectable entry really has a collector, every entry that is not collected
says why, and rows stored before the publications were split out still land on
the right one.
"""
from __future__ import annotations

import dataclasses

import pytest

import scraper
from api import admin_api
from loader.db import connect
from loader.ingest import init_db
from scraper import catalog
from scraper.publications import LEGACY_NEWSFEED_LABEL, label_for, source_id_for

ADMIN = {"email": "admin@example.com", "isAdmin": True}


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A temp database, with every route the endpoint takes to a database
    pointed at it. Unpatched, these would reach whatever DATABASE_URL names."""
    wl = tmp_path / "wl.json"
    wl.write_text("[]")
    path = tmp_path / "cat.db"
    init_db(path, watchlist_path=wl)
    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setattr("api.admin_api.connect", lambda t=None, **kw: connect(path, **kw))
    monkeypatch.setattr("loader.source_settings.connect",
                        lambda t=None, **kw: connect(path, **kw))
    monkeypatch.setattr("loader.pipeline_settings.connect",
                        lambda t=None, **kw: connect(path, **kw))
    monkeypatch.setattr(admin_api, "_integration_note", lambda src: ("connected", src.note))
    return path


# ---------- the catalogue is internally consistent ----------


def test_every_category_used_is_declared():
    declared = {key for key, _ in catalog.CATEGORIES}
    assert {s.category for s in catalog.SOURCES} <= declared


def test_every_section_of_the_guide_has_sources():
    used = {s.category for s in catalog.SOURCES}
    assert used == {key for key, _ in catalog.CATEGORIES}


def test_a_source_is_either_collected_or_says_why_not():
    for s in catalog.SOURCES:
        if s.collectable:
            assert s.availability is None, s.id
        else:
            assert s.availability in catalog.AVAILABILITY_LABEL, s.id
            assert s.note, f"{s.id} is not collected and does not say why"


def test_a_source_that_ships_off_says_why():
    for s in catalog.COLLECTED:
        if not s.default_enabled:
            assert s.off_reason, s.id


def test_every_feed_has_an_address_and_a_market():
    for s in catalog.FEEDS:
        assert s.feed_url and s.feed_url.startswith("https://"), s.id
        assert s.geography in ("AU", "PNG"), s.id


def test_source_types_are_ones_the_classifier_knows():
    from agents.signal_analyst import KIND_LABELS

    for s in catalog.COLLECTED:
        assert s.source_type in KIND_LABELS, s.id


# ---------- the registry is the catalogue ----------


def test_the_registry_has_a_collector_for_every_collectable_source():
    registry = scraper._registry()
    assert list(registry) == [s.id for s in catalog.COLLECTED]
    assert tuple(registry) == scraper.SOURCE_NAMES
    assert all(callable(fn) for fn in registry.values())


def test_the_original_sources_keep_their_stored_names():
    """Signals already in the database are stored under these. Renaming one
    would orphan its history as a "retired" source."""
    for name in ("pngworkforce", "seek", "adzuna", "newsfeed", "pngbusinessnews", "austender"):
        assert name in scraper.SOURCE_NAMES


# ---------- rows stored under the old shared name ----------


def test_an_old_newsfeed_row_is_attributed_to_its_publication():
    assert source_id_for("newsfeed", "https://www.australianmining.com.au/a-story/") == "australianmining"
    assert source_id_for("newsfeed", "https://mining.com.au/story") == "miningcomau"
    assert source_id_for("newsfeed", "https://www.businessadvantagepng.com/x/") == "businessadvantagepng"


def test_an_old_row_from_an_unknown_domain_stays_where_it_was():
    assert source_id_for("newsfeed", "https://some-custom-feed.example/story") == "newsfeed"
    assert source_id_for("newsfeed", None) == "newsfeed"


def test_other_sources_are_never_remapped():
    assert source_id_for("adzuna", "https://www.australianmining.com.au/x") == "adzuna"


def test_a_signal_is_named_by_its_publication_either_way():
    """Stored under the shared name or under its own, the reader sees one name."""
    old = label_for("newsfeed", "https://www.australianmining.com.au/a-story/")
    new = label_for("australianmining", "https://www.australianmining.com.au/another/")
    assert old == new == "Australian Mining"


def test_the_shared_name_alone_reads_as_older_runs():
    assert label_for("newsfeed") == LEGACY_NEWSFEED_LABEL


def test_a_retired_source_keeps_its_stored_name():
    assert label_for("some-removed-scraper") == "some-removed-scraper"


# ---------- the admin page ----------


def _add(db, *, source, url, captured="2026-10-05T05:00:00+00:00"):
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO signals (signal_id, source_type, source_name, source_url, captured_at, "
            "geography, raw_content, classified_at) VALUES (?,?,?,?,?,?,?,?)",
            (url, "news", source, url, captured, "AU", "x", captured),
        )


def test_the_page_lists_the_whole_guide_grouped_by_its_sections(db):
    payload = admin_api.source_health(ADMIN)
    assert [c["key"] for c in payload["categories"]] == [k for k, _ in catalog.CATEGORIES]
    assert {s["name"] for s in payload["sources"]} == {s.id for s in catalog.SOURCES}
    assert payload["collectableCount"] == len(catalog.COLLECTED)


def test_a_source_that_is_not_collected_carries_its_reason(db):
    rows = {s["name"]: s for s in admin_api.source_health(ADMIN)["sources"]}
    bci = rows["bcigem"]
    assert bci["collectable"] is False
    assert bci["status"] == "subscription"
    assert bci["statusLabel"] == "Subscription required"
    assert bci["note"]
    assert bci["enabled"] is False and bci["limit"] is None


def test_old_newsfeed_rows_count_towards_their_publication(db):
    _add(db, source="newsfeed", url="https://www.australianmining.com.au/one/")
    _add(db, source="newsfeed", url="https://www.australianmining.com.au/two/")
    _add(db, source="australianmining", url="https://www.australianmining.com.au/three/")
    _add(db, source="newsfeed", url="https://mining.com.au/four")

    rows = {s["name"]: s for s in admin_api.source_health(ADMIN)["sources"]}
    assert rows["australianmining"]["totalRecords"] == 3
    assert rows["miningcomau"]["totalRecords"] == 1
    assert rows["newsfeed"]["totalRecords"] == 0
    assert not any(s["status"] == "retired" for s in rows.values())


def test_a_board_waiting_on_an_actor_is_not_configured(db, monkeypatch):
    rows = {s["name"]: s for s in admin_api.source_health(ADMIN)["sources"]}
    assert rows["indeed"]["status"] == "not_configured"
    assert "Integrations" in rows["indeed"]["note"], "the note says where to fix it"
    assert rows["indeed"]["enabled"] is False
