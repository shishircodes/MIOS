"""API keys entered in the panel.

The keys these hold are bearer tokens that spend money, so the tests are mostly
about the ways a credential store leaks or lies rather than about round-tripping
a string:

* the key never comes back out through the API, only a hint;
* the ciphertext in the table is not the key;
* a deployment with no bootstrap secret refuses to store rather than quietly
  storing plaintext, and keeps working on its environment keys;
* a stored key that will not decrypt is reported as such rather than as absent,
  because those have completely different fixes.
"""
from __future__ import annotations

import pytest

from loader import credentials
from loader.credentials import (
    KEY_ENV,
    CredentialsLocked,
    clear_key,
    describe,
    key_for,
    set_key,
    stored_key,
    stored_rows,
)
from loader.db import connect
from loader.ingest import init_db

SECRET = "a-stable-bootstrap-secret"
KEY = "AIzaSyExampleKeyForTestsOnly-1234wxyz"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY_ENV, SECRET)
    # The derivation is cached on the secret; a test that changes it must not
    # inherit a cipher built for another one.
    credentials._fernet_for.cache_clear()
    path = tmp_path / "creds.db"
    init_db(path)
    return path


# ---------- the key goes in and does not come back ----------


def test_a_stored_key_is_readable_by_the_app(db):
    set_key("gemini", KEY, changed_by="admin@example.com", target=db)

    assert stored_key("gemini", db) == KEY


def test_what_is_written_to_the_table_is_not_the_key(db):
    """A database dump must not hand over working credentials."""
    set_key("gemini", KEY, changed_by="admin@example.com", target=db)

    with connect(db, readonly=True) as conn:
        row = conn.execute(
            "SELECT secret FROM llm_credentials WHERE provider = 'gemini'").fetchone()

    assert KEY not in row["secret"]
    # Not a substring either way: no prefix of the key survives in the token.
    assert KEY[:12] not in row["secret"]


def test_the_description_carries_a_hint_and_never_the_key(db):
    set_key("gemini", KEY, changed_by="admin@example.com", target=db)

    described = describe("gemini", "", target=db)

    assert described["hint"] == KEY[-4:]
    assert KEY not in str(described)
    assert described["source"] == "panel"
    assert described["changedBy"] == "admin@example.com"


# ---------- the environment is a layer, not a casualty ----------


def test_the_environment_key_is_used_when_nothing_is_stored(db):
    """A deployment nobody has opened the panel on behaves exactly as before."""
    assert key_for("gemini", "from-the-environment", target=db) == "from-the-environment"
    assert describe("gemini", "from-the-environment", target=db)["source"] == "environment"


def test_a_stored_key_wins_over_the_environment_and_says_so(db):
    """Same precedence as `llm_settings`: the panel is the more recent act. But
    an environment variable that appears to do nothing has to be visible, or
    somebody debugs it for an hour."""
    set_key("gemini", KEY, changed_by="admin@example.com", target=db)

    assert key_for("gemini", "from-the-environment", target=db) == KEY
    assert describe("gemini", "from-the-environment", target=db)["shadowsEnvironment"] is True


def test_clearing_a_key_returns_the_provider_to_the_environment(db):
    set_key("gemini", KEY, changed_by="admin@example.com", target=db)

    assert clear_key("gemini", target=db) is True

    assert key_for("gemini", "from-the-environment", target=db) == "from-the-environment"
    assert describe("gemini", "from-the-environment", target=db)["source"] == "environment"


def test_a_key_can_be_stored_before_the_schema_has_been_applied(tmp_path, monkeypatch):
    """The schema is applied by `init_db`, which runs during a pipeline run. A
    deployment that has not run since this shipped would otherwise refuse a key
    until the following Monday, for a reason nobody could act on.
    """
    monkeypatch.setenv(KEY_ENV, SECRET)
    credentials._fernet_for.cache_clear()
    path = tmp_path / "bare.db"
    with connect(path) as conn:
        conn.execute("CREATE TABLE placeholder (x TEXT)")

    set_key("gemini", KEY, changed_by="admin@example.com", target=path)

    assert stored_key("gemini", path) == KEY


def test_the_lazy_table_matches_the_one_in_the_schema(tmp_path, monkeypatch):
    """Two definitions of one table is a drift waiting to happen, so this
    compares what each actually produces rather than the SQL text."""
    monkeypatch.setenv(KEY_ENV, SECRET)
    credentials._fernet_for.cache_clear()

    from_schema = tmp_path / "schema.db"
    init_db(from_schema)

    lazily = tmp_path / "lazy.db"
    with connect(lazily) as conn:
        credentials._ensure_table(conn)

    def columns(path):
        with connect(path, readonly=True) as conn:
            return [(r[1], r[2]) for r in
                    conn.execute("PRAGMA table_info(llm_credentials)").fetchall()]

    assert columns(lazily) == columns(from_schema) != []


def test_a_missing_table_falls_back_rather_than_failing(tmp_path, monkeypatch):
    """A database created before this feature must not stop the pipeline
    reaching a model."""
    monkeypatch.setenv(KEY_ENV, SECRET)
    path = tmp_path / "old.db"
    with connect(path) as conn:
        conn.execute("CREATE TABLE placeholder (x TEXT)")

    assert stored_rows(path) == {}
    assert key_for("gemini", "from-the-environment", target=path) == "from-the-environment"


# ---------- a locked deployment refuses loudly ----------


def test_without_a_bootstrap_secret_storing_is_refused(db, monkeypatch):
    """Not "stored in plaintext". An administrator told the key was saved would
    reasonably believe it was protected."""
    monkeypatch.delenv(KEY_ENV, raising=False)
    credentials._fernet_for.cache_clear()

    with pytest.raises(CredentialsLocked) as exc:
        set_key("gemini", KEY, changed_by="admin@example.com", target=db)

    assert KEY_ENV in str(exc.value)
    with connect(db, readonly=True) as conn:
        assert conn.execute("SELECT count(*) FROM llm_credentials").fetchone()[0] == 0


def test_a_locked_deployment_still_uses_its_environment_keys(db, monkeypatch):
    """Refusing to store must not mean refusing to run."""
    monkeypatch.delenv(KEY_ENV, raising=False)
    credentials._fernet_for.cache_clear()

    assert key_for("gemini", "from-the-environment", target=db) == "from-the-environment"
    assert describe("gemini", "from-the-environment", target=db)["canStore"] is False


def test_an_empty_key_is_refused(db):
    with pytest.raises(Exception):
        set_key("gemini", "   ", changed_by="admin@example.com", target=db)


# ---------- a rotated bootstrap secret is diagnosed, not silently empty ----------


def test_a_key_encrypted_under_a_different_secret_reads_as_unreadable(db, monkeypatch):
    """The symptom is identical to "no key at all" and the fix is completely
    different: restore the old MIOS_CREDENTIAL_KEY, or re-enter the API key."""
    set_key("gemini", KEY, changed_by="admin@example.com", target=db)

    monkeypatch.setenv(KEY_ENV, "a-different-secret")
    credentials._fernet_for.cache_clear()

    assert stored_key("gemini", db) is None
    described = describe("gemini", "", target=db)
    assert described["unreadable"] is True
    assert described["source"] == "none"


def test_an_unreadable_stored_key_falls_back_to_the_environment(db, monkeypatch):
    set_key("gemini", KEY, changed_by="admin@example.com", target=db)

    monkeypatch.setenv(KEY_ENV, "a-different-secret")
    credentials._fernet_for.cache_clear()

    assert key_for("gemini", "from-the-environment", target=db) == "from-the-environment"


# ---------- providers read through it ----------


def test_a_provider_reports_itself_configured_from_a_stored_key(db, monkeypatch):
    """The point of the feature: a key typed in the panel makes the provider
    usable without a redeploy."""
    from config.settings import settings
    from llm.providers import AnthropicProvider

    # `settings` is a frozen dataclass, so this asserts the precondition rather
    # than arranging it: with no ANTHROPIC_API_KEY in the test environment, the
    # only key that can make this provider configured is the stored one.
    assert not settings.anthropic_api_key
    monkeypatch.setattr("loader.db.resolve_target", lambda *_a, **_k: str(db))

    provider = AnthropicProvider()
    assert provider.configured() is False

    set_key("anthropic", "sk-ant-example", changed_by="admin@example.com", target=db)
    assert provider.configured() is True
