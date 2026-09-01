"""Every model call goes through one place, and is counted there.

The seam was added so usage could be counted where no caller could sidestep it,
and then three of the four callers were left building their own client. The
counter it fed therefore reported Mode Push only, while the pipeline's
classification and Market Pulse calls — the ones that actually spend the weekly
allowance — were invisible.

These pin the property rather than the plumbing: no module reaches a provider
except through `llm.caller_for`, and a call through it is counted whether it
succeeds or fails.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from llm import PURPOSES
from llm.providers import LLMError, QuotaExhausted

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Modules allowed to construct a provider client. Only the seam itself.
MAY_BUILD_A_CLIENT = {"llm/providers.py"}


def _modules():
    for pkg in ("agents", "api", "delivery", "loader", "pipeline", "publish", "push", "llm"):
        for path in (ROOT / pkg).rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path


def test_nothing_outside_the_seam_constructs_a_model_client():
    """The check that would have caught three callers being left behind."""
    offenders = []
    for path in _modules():
        rel = path.relative_to(ROOT).as_posix()
        if rel in MAY_BUILD_A_CLIENT:
            continue
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            # `from google import genai` / `import anthropic` outside the seam.
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("google"):
                offenders.append(f"{rel}: imports {node.module}")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in {"anthropic"}:
                        offenders.append(f"{rel}: imports {a.name}")

    assert not offenders, (
        "these reach a provider directly instead of through llm.caller_for, so "
        "their calls are not counted and cannot be routed to another model:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("purpose", sorted(PURPOSES))
def test_every_purpose_resolves(purpose):
    from llm import resolve

    provider, model = resolve(purpose)
    assert provider and model


def test_a_successful_call_is_counted(tmp_path, monkeypatch):
    import llm.providers as providers
    import llm.usage as usage

    recorded = []
    monkeypatch.setattr(usage, "record",
                        lambda *a, **k: recorded.append((a, k.get("ok"))))
    monkeypatch.setattr(providers._PROVIDERS["gemini"], "build",
                        lambda model: (lambda s, u, sc=None: {"ok": True}))

    providers.caller_for("classify")("system", "user", None)
    assert len(recorded) == 1
    assert recorded[0][1] is True


def test_a_failed_call_is_counted_too(monkeypatch):
    """The reason the old counter read zero on a day the allowance ran out: a
    provider charges for a rejected request the same as a served one."""
    import llm.providers as providers
    import llm.usage as usage

    recorded = []
    monkeypatch.setattr(usage, "record",
                        lambda *a, **k: recorded.append((a, k.get("ok"))))

    def _boom(model):
        def _call(s, u, sc=None):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return _call

    monkeypatch.setattr(providers._PROVIDERS["gemini"], "build", _boom)

    with pytest.raises(QuotaExhausted):
        providers.caller_for("pulse")("system", "user", None)

    assert len(recorded) == 1
    assert recorded[0][1] is False


def test_only_a_quota_refusal_is_retyped(monkeypatch):
    """A spent allowance becomes `QuotaExhausted` so callers can say "try later"
    rather than "something broke". Everything else propagates unchanged — losing
    the original type would make a genuine bug look like a model having a bad
    day, which is the confusion this seam was meant to end."""
    import llm.providers as providers
    import llm.usage as usage

    monkeypatch.setattr(usage, "record", lambda *a, **k: None)
    monkeypatch.setattr(providers._PROVIDERS["gemini"], "build",
                        lambda model: (lambda s, u, sc=None: (_ for _ in ()).throw(
                            ValueError("malformed response"))))

    with pytest.raises(ValueError):
        providers.caller_for("push")("system", "user", None)
