"""Tests for role-based access (api.access) and the admin endpoints.

What is being pinned here is the policy, not the plumbing: who gets in, by which
of the three routes, who reaches the Admin section, and which changes must be
refused because they would lock everybody out.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from api import access
from loader.db import connect
from loader.ingest import init_db


@pytest.fixture
def db(tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps([]))
    path = tmp_path / "access.db"
    init_db(path, watchlist_path=wl)
    # init_db seeds the bootstrap admin; most tests want to start from empty so
    # they can build the exact situation under test.
    with connect(path) as conn:
        conn.execute("DELETE FROM app_users")
    return path


@pytest.fixture
def policy(monkeypatch):
    """A settings stand-in, so tests state the sign-in policy rather than
    inheriting whatever the developer's .env happens to say."""
    def _set(domain: str = "", emails: tuple[str, ...] = ()):
        s = type("S", (), {"allowed_google_domain": domain, "allowed_emails": list(emails)})()
        monkeypatch.setattr("api.access.settings", s)
        return s
    return _set


# ---------- normalising ----------


def test_addresses_are_matched_case_and_space_insensitively(db):
    access.upsert_user("  Bijay@Example.COM ", "member", added_by="me", target=db)
    assert access.get_user("bijay@example.com", db) is not None
    assert access.get_user("BIJAY@EXAMPLE.COM", db) is not None


def test_a_string_that_is_not_an_address_is_refused(db):
    with pytest.raises(access.AccessError):
        access.upsert_user("not-an-email", "member", added_by="me", target=db)


def test_an_unknown_role_is_refused(db):
    with pytest.raises(access.AccessError):
        access.upsert_user("a@b.com", "superuser", added_by="me", target=db)


# ---------- the three doors ----------


def test_a_named_row_admits_any_domain(db, policy):
    """The point of the table: somebody outside the Workspace, admitted by name
    without widening the domain rule for everyone at that domain."""
    policy(domain="easyskill.com")
    assert access.role_for("outsider@gmail.com", hd=None, target=db) is None

    access.upsert_user("outsider@gmail.com", "member", added_by="admin", target=db)
    assert access.role_for("outsider@gmail.com", hd=None, target=db) == "member"


def test_the_workspace_domain_admits_members_only(db, policy):
    policy(domain="easyskill.com")
    assert access.role_for("staff@easyskill.com", hd="easyskill.com", target=db) == "member"
    assert access.is_admin("staff@easyskill.com", db) is False


def test_the_domain_is_checked_against_the_verified_claim_not_the_address(db, policy):
    """An attacker controls the local part and can register any address they
    like; only the `hd` claim is asserted by Google."""
    policy(domain="easyskill.com")
    assert access.role_for("someone@easyskill.com", hd="evil.example", target=db) is None


def test_the_environment_allowlist_still_admits_people(db, policy):
    """Removing this would lock out whoever ALLOWED_EMAILS names on the next
    deploy, so it stays honoured."""
    policy(domain="easyskill.com", emails=("legacy@gmail.com",))
    assert access.role_for("legacy@gmail.com", hd=None, target=db) == "member"


def test_a_named_admin_row_beats_the_environment_allowlist(db, policy):
    """Both doors name the same person. The table is the one that can say
    'admin', so it must win rather than being masked by the env grant."""
    policy(domain="easyskill.com", emails=("shishir@gmail.com",))
    access.upsert_user("shishir@gmail.com", "admin", added_by="system", target=db)
    assert access.role_for("shishir@gmail.com", hd=None, target=db) == "admin"


def test_nobody_is_admitted_when_a_policy_is_configured_and_matches_nothing(db, policy):
    policy(domain="easyskill.com", emails=("someone@gmail.com",))
    assert access.role_for("stranger@elsewhere.com", hd="elsewhere.com", target=db) is None


# ---------- the environment door is visible ----------


def test_env_grants_are_listed_so_an_admin_can_see_them(policy):
    """An access route nobody can see is a route nobody revokes."""
    policy(emails=("legacy@gmail.com", "Contractor@Other.COM"))
    grants = access.env_grants()
    assert [g["email"] for g in grants] == ["legacy@gmail.com", "contractor@other.com"]
    assert all(g["source"] == "environment" for g in grants)
    assert all(g["role"] == "member" for g in grants), "the env door never grants admin"


def test_env_grants_are_marked_as_not_editable_here(policy):
    policy(emails=("legacy@gmail.com",))
    (grant,) = access.env_grants()
    assert grant["source"] != "database"
    assert "ALLOWED_EMAILS" in grant["note"]


def test_an_empty_allowlist_lists_nothing(policy):
    policy(emails=())
    assert access.env_grants() == []


# ---------- bootstrap ----------


def test_the_first_admin_is_seeded_so_the_app_is_never_locked(db):
    assert access.count_admins(db) == 0
    access.ensure_bootstrap_admin(db)
    assert access.is_admin(access.BOOTSTRAP_ADMIN, db) is True


def test_seeding_twice_changes_nothing(db):
    access.ensure_bootstrap_admin(db)
    access.ensure_bootstrap_admin(db)
    assert access.count_admins(db) == 1


def test_seeding_does_not_resurrect_a_deliberate_demotion(db):
    """Somebody demoted the bootstrap account and promoted a colleague. A
    restart must not quietly hand the original its role back."""
    access.ensure_bootstrap_admin(db)
    access.upsert_user("bijay@easyskill.com", "admin", added_by="boot", target=db)
    access.upsert_user(access.BOOTSTRAP_ADMIN, "member", added_by="bijay", target=db)

    access.ensure_bootstrap_admin(db)

    assert access.is_admin(access.BOOTSTRAP_ADMIN, db) is False
    assert access.is_admin("bijay@easyskill.com", db) is True


# ---------- the last-admin guards ----------


def test_the_only_admin_cannot_be_demoted(db):
    access.upsert_user("solo@easyskill.com", "admin", added_by="system", target=db)
    with pytest.raises(access.AccessError, match="only administrator"):
        access.upsert_user("solo@easyskill.com", "member", added_by="solo", target=db)
    assert access.is_admin("solo@easyskill.com", db) is True


def test_the_only_admin_cannot_be_removed(db):
    access.upsert_user("solo@easyskill.com", "admin", added_by="system", target=db)
    with pytest.raises(access.AccessError, match="only administrator"):
        access.remove_user("solo@easyskill.com", removed_by="solo", target=db)


def test_an_admin_can_step_down_once_someone_else_is_promoted(db):
    access.upsert_user("a@easyskill.com", "admin", added_by="system", target=db)
    access.upsert_user("b@easyskill.com", "admin", added_by="a", target=db)
    access.upsert_user("a@easyskill.com", "member", added_by="a", target=db)
    assert access.count_admins(db) == 1


def test_removing_somebody_who_was_never_added_reports_it(db):
    assert access.remove_user("ghost@nowhere.com", removed_by="admin", target=db) is False


# ---------- upsert semantics ----------


def test_adding_an_existing_address_changes_the_role_rather_than_duplicating(db):
    access.upsert_user("a@easyskill.com", "member", added_by="system", target=db)
    access.upsert_user("a@easyskill.com", "admin", added_by="system", target=db)
    assert len(access.list_users(db)) == 1
    assert access.is_admin("a@easyskill.com", db) is True


def test_admins_are_listed_first(db):
    access.upsert_user("zoe@easyskill.com", "member", added_by="s", target=db)
    access.upsert_user("alan@easyskill.com", "member", added_by="s", target=db)
    access.upsert_user("yuri@easyskill.com", "admin", added_by="s", target=db)
    assert [u["email"] for u in access.list_users(db)] == [
        "yuri@easyskill.com", "alan@easyskill.com", "zoe@easyskill.com",
    ]


def test_a_sign_in_is_recorded_so_unused_grants_are_visible(db):
    access.upsert_user("a@easyskill.com", "member", added_by="s", target=db)
    assert access.get_user("a@easyskill.com", db)["lastSeen"] is None
    access.touch_last_seen("a@easyskill.com", db)
    assert access.get_user("a@easyskill.com", db)["lastSeen"] is not None


def test_recording_a_sign_in_for_a_domain_user_is_harmless(db):
    """Somebody admitted by the Workspace rule has no row. Recording their
    sign-in must not raise, and must not invent a grant for them."""
    access.touch_last_seen("staff@easyskill.com", db)
    assert access.list_users(db) == []


# ---------- the API refuses, not just the UI ----------


def _auth_settings(monkeypatch, *, disabled: bool):
    """Settings is a frozen dataclass, so the module-level reference is swapped
    rather than mutated. current_user and require_admin both read this one."""
    from api import auth

    monkeypatch.setattr(auth, "settings", type("S", (), {"auth_disabled": disabled})())
    return auth


def test_require_admin_refuses_a_member(monkeypatch):
    """Hiding the nav group is presentation. This is the part that stops a
    member reading the access list by typing the URL."""
    auth = _auth_settings(monkeypatch, disabled=False)
    monkeypatch.setattr(auth, "require_user", lambda _r: {"email": "member@easyskill.com"})
    monkeypatch.setattr(access, "is_admin", lambda e, t=None: False)

    with pytest.raises(HTTPException) as exc:
        auth.require_admin(request=None)
    assert exc.value.status_code == 403


def test_require_admin_lets_an_admin_through(monkeypatch):
    auth = _auth_settings(monkeypatch, disabled=False)
    monkeypatch.setattr(auth, "require_user", lambda _r: {"email": "boss@easyskill.com"})
    monkeypatch.setattr(access, "is_admin", lambda e, t=None: True)

    assert auth.require_admin(request=None)["email"] == "boss@easyskill.com"


def test_the_dev_bypass_is_an_admin_consistently(monkeypatch):
    """With AUTH_DISABLED the API serves admin endpoints, so /api/me must report
    the same thing — otherwise the nav hides a section that would have worked."""
    auth = _auth_settings(monkeypatch, disabled=True)
    assert auth.current_user(request=None)["role"] == access.ROLE_ADMIN
    assert auth.require_admin(request=None)["role"] == access.ROLE_ADMIN


# ---------- "domain accounts only" must not be claimed while it is false ----------
#
# Two callers ask whether anyone outside the domain can sign in: the login route,
# which filters Google's account chooser, and /api/me, which writes the line on
# the sign-in screen. Both used to read `bool(ALLOWED_EMAILS)`, so leaving that
# empty made both of them wrong.


def _external(db, *, domain="easyskill.com", emails=()):
    return access.has_external_grants(domain=domain, allowed_emails=emails, target=db)


def test_an_env_allowlist_counts_as_an_exception(db):
    assert _external(db, emails=("legacy@gmail.com",)) is True


def test_a_named_outsider_counts_even_with_an_empty_allowlist(db):
    """The regression: ALLOWED_EMAILS is empty, but a contractor was granted a
    row. Reporting 'no exceptions' would hide them from the account chooser and
    tell them on the sign-in page that they cannot get in."""
    access.upsert_user("contractor@gmail.com", "member", added_by="boss", target=db)
    assert _external(db) is True


def test_staff_rows_are_not_exceptions(db):
    """Naming somebody at the Workspace domain — to make them an admin, say —
    grants nothing the domain rule did not already grant, so the chooser should
    still be filtered."""
    access.upsert_user("boss@easyskill.com", "admin", added_by="system", target=db)
    assert _external(db) is False


def test_no_exceptions_when_nothing_but_the_domain_is_configured(db):
    assert _external(db) is False


def test_the_allowlist_short_circuits_before_the_database(db, monkeypatch):
    """This runs on the sign-in redirect. When the environment already answers
    the question there is no reason to touch the database at all."""
    def _boom(*_a, **_k):
        raise AssertionError("queried the database unnecessarily")

    monkeypatch.setattr(access, "count_external_grants", _boom)
    assert _external(db, emails=("legacy@gmail.com",)) is True


def test_an_unreadable_database_reports_no_exceptions(db, monkeypatch):
    """Presentation only, and a table-granted sign-in would be failing anyway if
    this query is failing. Better than raising on the sign-in screen."""
    def _broken(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(access, "connect", _broken)
    assert _external(db) is False


def test_the_policy_comes_from_the_caller_not_a_second_settings_object(db):
    """api.auth holds its own reference to settings. Reading `settings` inside
    access.py instead meant an override applied by the caller never arrived —
    which is exactly how this went wrong the first time."""
    access.upsert_user("contractor@gmail.com", "member", added_by="boss", target=db)
    assert _external(db, domain="other.example") is True, "unrelated domain: everyone is external"
    assert _external(db, domain="gmail.com") is False, "same domain: nobody is"
