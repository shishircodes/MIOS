"""Reading configuration from the environment.

One property, and it cost a week of PNG collection to learn: **an empty value is
not a value.**

`deploy-main.yml` writes the server's `.env` from GitHub repository variables,
and an unset variable expands to nothing — so the file gets
`PNGWORKFORCE_BASE_URL=`, present and empty. Under `os.environ.get(name,
default)` that beats the default, because the default applies only when the name
is *missing*. The scraper then had no URL, returned an empty list, and the run
reported healthy while collecting nothing.

It worked on every laptop, where the variable is genuinely absent. That is the
shape of the bug and the reason it survived being "verified": the fix was
checked in the one state the same commit made impossible in production.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from config.settings import _get, _get_list, load_settings

PNG_DEFAULT = "https://www.pngworkforce.com/jobs/view-latest-jobs"


# ---------- the property ----------


def test_a_missing_variable_uses_the_default(monkeypatch):
    monkeypatch.delenv("SOME_SETTING", raising=False)
    assert _get("SOME_SETTING", "fallback") == "fallback"


def test_an_empty_variable_also_uses_the_default(monkeypatch):
    """The whole bug. `os.environ.get(name, default)` returns "" here, because
    the name is present — and "" then overrides a default that was right."""
    monkeypatch.setenv("SOME_SETTING", "")
    assert _get("SOME_SETTING", "fallback") == "fallback"


def test_a_whitespace_only_variable_uses_the_default(monkeypatch):
    """A heredoc writing `NAME= ` is the same accident with a trailing space."""
    monkeypatch.setenv("SOME_SETTING", "   ")
    assert _get("SOME_SETTING", "fallback") == "fallback"


def test_a_real_value_still_wins(monkeypatch):
    monkeypatch.setenv("SOME_SETTING", "chosen")
    assert _get("SOME_SETTING", "fallback") == "chosen"


def test_a_value_is_stripped(monkeypatch):
    monkeypatch.setenv("SOME_SETTING", "  chosen  ")
    assert _get("SOME_SETTING", "fallback") == "chosen"


def test_with_no_default_an_empty_variable_is_still_empty(monkeypatch):
    """Nothing gains a value it did not have: most settings default to "" and
    must keep reading as unset."""
    monkeypatch.setenv("SOME_SETTING", "")
    assert _get("SOME_SETTING") == ""


# ---------- the setting that actually broke ----------


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_scraper_url_falls_back_to_the_listings_page(monkeypatch, value):
    """The 6 September run collected zero PNG job signals because this returned
    "" and the scraper has nowhere to go."""
    monkeypatch.setenv("PNGWORKFORCE_BASE_URL", value)

    assert load_settings().pngworkforce_base_url == PNG_DEFAULT


def test_an_empty_seek_url_falls_back_too(monkeypatch):
    """SEEK carries the identical exposure and has only been spared by being
    switched off. Turning it on must not reintroduce the same silent zero."""
    monkeypatch.setenv("SEEK_BASE_URL", "")

    assert load_settings().seek_base_url == "https://au.seek.com"


def test_the_deployment_shape_is_what_gets_tested():
    """The failing case reproduced end to end, in a fresh interpreter.

    `deploy-main.yml` writes `NAME=` for an unset GitHub variable and compose
    passes it through, so the container sees the name set to "". Asserting it
    here rather than only through monkeypatch, because the previous fix was
    verified with the variable *removed* — which is not the state the deployment
    produces, and is why the bug shipped.
    """
    code = ("from config.settings import load_settings;"
            "print(load_settings().pngworkforce_base_url)")
    import os

    env = {k: v for k, v in os.environ.items()}
    env["PNGWORKFORCE_BASE_URL"] = ""
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True)
    assert out.stdout.strip() == PNG_DEFAULT, out.stderr


# ---------- the list version already did this ----------


def test_an_empty_list_variable_means_use_the_code_default(monkeypatch):
    """`_get_list` has always treated empty as "unset". The scalar version is now
    consistent with it; this pins that they agree."""
    monkeypatch.setenv("SEEK_PATHS", "")
    assert _get_list("SEEK_PATHS") == ()
