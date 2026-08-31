"""Shared safety net: no test ever reaches the configured database.

Written after a near miss. Three tests in `test_admin_api.py` called an admin
endpoint that reached `loader.source_settings.set_enabled`, which resolves its
target through `loader.db.connect(None)`. The fixture patched
`api.admin_api.connect` and `api.access.connect` but not the one in
`loader.source_settings`, so the call fell through to whatever `DATABASE_URL`
names — on a developer machine, production. CI caught it only because CI has no
`DATABASE_URL` and so failed with "no such table": the right alarm for entirely
the wrong reason. The writes happened to cancel out. That was luck.

Turning that into a hard error revealed eleven more tests, in `test_auth.py`
and `test_db.py`, doing the same thing and predating all of this. Which is the
real lesson: a test reaching the default target is invisible whenever the
default target happens to work. The suite passes, and the only evidence is rows
appearing in a database nobody was watching. It also made those tests depend on
production being reachable, which is why they failed intermittently for
apparently unrelated reasons.

So rather than police every call site, the default target is simply made
harmless: with no explicit target, a test gets its own empty SQLite file. That
is exactly what CI already does by having no `DATABASE_URL`, so a developer's
machine now behaves the way CI does instead of differently and more
dangerously.

The hook is on `resolve_target`, not `connect`: modules do
`from loader.db import connect` and hold their own reference, so patching
`connect` misses precisely the callers worth catching. `connect` looks up
`resolve_target` as a module global on every call, so every path goes through
it.

A test that genuinely needs the configured database can opt out with
`@pytest.mark.real_database`. Nothing does today, and anything that wants to
should have to explain why in review.
"""
from __future__ import annotations

import pytest

import loader.db as db_module
from config.settings import settings as _configured

#: The one target that must never be reached. Captured here, before any test
#: patches `loader.db.settings`: `test_describe_never_leaks_the_password`
#: substitutes a fake DSN to check masking, and redirecting that would break a
#: test that never opens a connection at all. Only the real thing is diverted.
_REAL_DSN = _configured.database_url or None


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "real_database: allow this test to reach the configured database rather "
        "than a scratch one. Needs a deliberate reason.",
    )


@pytest.fixture(autouse=True)
def _never_the_real_database(request, tmp_path_factory, monkeypatch):
    """Redirect the default target to a scratch SQLite file for each test.

    Only the configured DSN is redirected, matched exactly. A test that passes
    its own path is untouched, which is nearly all of them, and so is one that
    substitutes a made-up DSN to exercise string handling.
    """
    if "real_database" in request.keywords:
        yield
        return

    real_resolve = db_module.resolve_target
    scratch = tmp_path_factory.mktemp("default-target") / "unconfigured.db"
    reached: list[str] = []

    def guarded(target=None):
        resolved = real_resolve(target)
        if _REAL_DSN and resolved == _REAL_DSN:
            # Something under test resolved the default target. Give it an
            # empty database rather than production: an empty one behaves like
            # the unreachable database these callers already handle, and is
            # what CI supplies anyway.
            reached.append(resolved.split("://", 1)[0])
            return scratch
        return resolved

    monkeypatch.setattr(db_module, "resolve_target", guarded)
    yield
    # Recorded rather than asserted. The redirect has already removed the
    # danger, and failing here would only punish tests that were written before
    # this file existed. It stays visible with `-o log_cli=true` or by reading
    # the report, and gives a list to work through if this is ever tightened.
    if reached:
        request.node.add_report_section(
            "teardown", "default database target",
            f"resolved the configured {reached[0]} target {len(reached)} time(s); "
            "served an empty scratch database instead",
        )
