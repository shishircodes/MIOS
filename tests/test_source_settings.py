"""Tests for admin-chosen scrape sources (loader.source_settings).

The design rests on two decisions, and the tests are mostly about those:

* **A source with no row sits at its default.** Only deviations are stored, so a
  scraper added later is collected from without a migration or a seed. Most
  sources default to on; SEEK defaults to off, because it refuses the deployed
  server's IP outright.
* **An empty selection means "none", never "all".** `scrape_all` reads an empty
  list as falsy and falls back to every source, so switching everything off has
  to be handled before it gets that far — otherwise pausing collection would
  start a full scrape instead.
"""
from __future__ import annotations

from typing import Any

import json

import pytest

from loader.db import connect
from loader.ingest import init_db
from loader.source_settings import (
    UnknownSource,
    enabled_sources,
    list_settings,
    set_enabled,
)
from scraper import SOURCE_NAMES


@pytest.fixture
def db(tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    path = tmp_path / "sources.db"
    init_db(path, watchlist_path=wl)
    return path


# ---------- the default is on ----------


def test_sources_start_at_their_default(db):
    """Every source that works is on; SEEK is not, because it cannot collect
    from where MIOS is deployed."""
    assert enabled_sources(db) == [n for n in SOURCE_NAMES if n != "seek"]
    assert "seek" not in enabled_sources(db)


def test_a_source_nobody_has_touched_still_appears_in_the_listing(db):
    """The listing is built from the registry, not the table — otherwise a
    source would be invisible in the panel until somebody toggled it."""
    settings = list_settings(db)
    assert set(settings) == set(SOURCE_NAMES)
    assert all(v["enabled"] for k, v in settings.items() if k != "seek")


def test_a_source_that_ships_off_explains_itself(db):
    """A default nobody can explain is one the next person quietly reverts,
    waits a week, and then re-diagnoses from scratch."""
    row = list_settings(db)["seek"]

    assert row["enabled"] is False
    assert row["defaultEnabled"] is False
    assert row["offReason"], "SEEK is off with no stated reason"
    assert "403" in row["offReason"], "the reason should name the observed cause"


def test_a_working_source_carries_no_reason(db):
    """The field is for explaining an exception, so it must be empty for every
    source that is not one."""
    row = list_settings(db)["adzuna"]
    assert row["defaultEnabled"] is True
    assert row["offReason"] is None


def test_returning_to_the_default_removes_the_row(db):
    """The table records deviations, so agreeing with the default is stored as
    nothing. That is what lets a default be revised later without rewriting
    every row that happened to agree with the old one."""
    set_enabled("adzuna", False, changed_by="admin", target=db)
    set_enabled("adzuna", True, changed_by="admin", target=db)

    with connect(db, readonly=True) as conn:
        n = conn.execute(
            "SELECT count(*) FROM source_settings WHERE source_name = 'adzuna'"
        ).fetchone()[0]
    assert n == 0
    assert "adzuna" in enabled_sources(db)


def test_switching_on_a_source_that_ships_off_is_stored_as_a_deviation(db):
    """The mirror image: for SEEK, "on" is the deviation, and the absence of a
    row cannot express it."""
    set_enabled("seek", True, changed_by="boss@easyskill.com", target=db)

    with connect(db, readonly=True) as conn:
        row = conn.execute(
            "SELECT enabled FROM source_settings WHERE source_name = 'seek'"
        ).fetchone()
    assert row is not None, "an explicit 'on' was not recorded"
    assert bool(row["enabled"]) is True
    assert "seek" in enabled_sources(db)


def test_an_admin_can_switch_a_shipped_off_source_back_off(db):
    set_enabled("seek", True, changed_by="a", target=db)
    set_enabled("seek", False, changed_by="a", target=db)

    assert "seek" not in enabled_sources(db)
    with connect(db, readonly=True) as conn:
        n = conn.execute("SELECT count(*) FROM source_settings").fetchone()[0]
    assert n == 0, "back at the default, so nothing should be stored"


# ---------- turning things off ----------


def test_a_disabled_source_drops_out_of_the_selection(db):
    set_enabled("adzuna", False, changed_by="admin", target=db)
    assert "adzuna" not in enabled_sources(db)
    assert "pngworkforce" in enabled_sources(db)


def test_the_selection_keeps_registry_order(db):
    """The order the pipeline scrapes in should not depend on what happens to
    be in the settings table."""
    set_enabled("seek", True, changed_by="admin", target=db)
    set_enabled("adzuna", False, changed_by="admin", target=db)
    assert enabled_sources(db) == [n for n in SOURCE_NAMES if n != "adzuna"]


def test_who_switched_it_off_is_recorded(db):
    """The question this table exists to answer is "why did we collect nothing
    from this source last week?", which a bare boolean cannot."""
    set_enabled("adzuna", False, changed_by="boss@easyskill.com", note="too noisy", target=db)

    row = list_settings(db)["adzuna"]
    assert row["enabled"] is False
    assert row["changedBy"] == "boss@easyskill.com"
    assert row["note"] == "too noisy"
    assert row["changedAt"]


def test_toggling_twice_updates_rather_than_duplicating(db):
    set_enabled("adzuna", False, changed_by="a", target=db)
    set_enabled("adzuna", False, changed_by="b", note="second", target=db)

    with connect(db, readonly=True) as conn:
        n = conn.execute("SELECT count(*) FROM source_settings").fetchone()[0]
    assert n == 1
    assert list_settings(db)["adzuna"]["changedBy"] == "b"


def test_an_unregistered_source_is_refused(db):
    """Otherwise a typo silently creates a row that disables nothing and
    reappears in the panel as a source that does not exist."""
    with pytest.raises(UnknownSource, match="not a registered source"):
        set_enabled("seeek", False, changed_by="admin", target=db)


# ---------- the empty case, which is the dangerous one ----------


def test_switching_everything_off_yields_an_empty_selection(db):
    for name in SOURCE_NAMES:
        set_enabled(name, False, changed_by="admin", target=db)
    assert enabled_sources(db) == []


def test_an_empty_selection_never_reaches_scrape_all(db, monkeypatch):
    """`scrape_all` treats an empty list as falsy and falls back to *every*
    source. Passing `[]` down would turn "collect from nothing" into "collect
    from everything" — so the pipeline must skip the fetch instead."""
    import pipeline.live as live

    for name in SOURCE_NAMES:
        set_enabled(name, False, changed_by="admin", target=db)

    calls: list[Any] = []
    monkeypatch.setattr(live, "scrape_all", lambda **kw: calls.append(kw) or [])
    monkeypatch.setattr(live, "classify_pending", lambda *a, **k: {})
    monkeypatch.setattr(live, "build_digest", lambda *a, **k: "")
    monkeypatch.setattr(live, "build_digest_payload", lambda *a, **k: {"collection": {"collected": 0}})
    monkeypatch.setattr(live, "generate_pulse",
                        lambda *a, **k: type("O", (), {"ok": False, "note": "n/a", "bullets": []})())
    monkeypatch.setattr(live, "save_pulse", lambda *a, **k: None)

    live.run_live_cycle(db_path=db, do_slack=False)
    assert calls == [], "an empty selection was passed down and became 'all sources'"


def test_a_command_line_source_overrides_the_stored_selection(db, monkeypatch):
    """Naming a source explicitly is a deliberate override, not a default to be
    second-guessed — including when everything is switched off."""
    import pipeline.live as live

    for name in SOURCE_NAMES:
        set_enabled(name, False, changed_by="admin", target=db)

    seen: list[list[str] | None] = []
    monkeypatch.setattr(live, "scrape_all",
                        lambda limit=None, sources=None, base_url=None: seen.append(sources) or [])
    monkeypatch.setattr(live, "classify_pending", lambda *a, **k: {})
    monkeypatch.setattr(live, "build_digest", lambda *a, **k: "")
    monkeypatch.setattr(live, "build_digest_payload", lambda *a, **k: {"collection": {"collected": 0}})
    monkeypatch.setattr(live, "generate_pulse",
                        lambda *a, **k: type("O", (), {"ok": False, "note": "n/a", "bullets": []})())
    monkeypatch.setattr(live, "save_pulse", lambda *a, **k: None)

    live.run_live_cycle(db_path=db, do_slack=False, sources=["seek"])
    assert seen == [["seek"]]


def test_only_the_enabled_sources_are_scraped(db, monkeypatch):
    import pipeline.live as live

    # seek is already off by default; switch off one more.
    set_enabled("adzuna", False, changed_by="admin", target=db)

    seen: list[list[str] | None] = []
    monkeypatch.setattr(live, "scrape_all",
                        lambda limit=None, sources=None, base_url=None: seen.append(sources) or [])
    monkeypatch.setattr(live, "classify_pending", lambda *a, **k: {})
    monkeypatch.setattr(live, "build_digest", lambda *a, **k: "")
    monkeypatch.setattr(live, "build_digest_payload", lambda *a, **k: {"collection": {"collected": 0}})
    monkeypatch.setattr(live, "generate_pulse",
                        lambda *a, **k: type("O", (), {"ok": False, "note": "n/a", "bullets": []})())
    monkeypatch.setattr(live, "save_pulse", lambda *a, **k: None)

    live.run_live_cycle(db_path=db, do_slack=False)
    assert seen == [["pngworkforce", "newsfeed"]]


# ---------- failing open ----------


def test_an_unreadable_table_leaves_every_source_enabled(db, monkeypatch):
    """A database hiccup must not silently stop collection. Failing closed here
    would be indistinguishable from an administrator pausing it."""
    import loader.source_settings as ss

    def _broken(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ss, "connect", _broken)
    assert enabled_sources(db) == [n for n in SOURCE_NAMES if n != "seek"]
