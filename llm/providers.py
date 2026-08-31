"""Providers, and the routing from a purpose to one of them.

A provider is anything that can answer `(system, user, schema) -> dict`. That
signature is not a design choice made here — it is the shape every existing call
site already uses, so adopting this package costs those callers nothing.

Adding Anthropic is adding a class below and a name to `_PROVIDERS`. The modules
that classify signals, write the Market Pulse or rank companies do not change,
because they never name a provider.

Two things this seam exists to fix, both of which have already cost a day:

**Quota was counted at one call site.** The daily counter lived inside
`classify_pending` and incremented only after a *successful* batch. Market Pulse
called Gemini directly and was never counted; a failed call was never counted
either, though Google counts every attempt. So the counter read zero on a day
the free tier was exhausted. Counting happens here now, around every call.

**A provider that is not configured looked like a provider that failed.** A
missing API key raised from deep inside a caller and was caught by a broad
handler that treated it as "the model is having a bad day". They need different
answers: one is a deployment problem, the other is a Tuesday.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Protocol

from config.settings import settings
from llm.purposes import PURPOSES, Purpose

log = logging.getLogger(__name__)

#: (system_prompt, user_prompt, schema) -> parsed JSON. The shape every existing
#: caller already passes around.
Caller = Callable[..., Any]


class LLMError(RuntimeError):
    """Anything that went wrong reaching a model."""


class ProviderNotConfigured(LLMError):
    """No credentials for this provider. A deployment problem, not a bad day."""


class QuotaExhausted(LLMError):
    """The provider refused because the allowance is spent."""


class Provider(Protocol):
    """What a provider must offer. Deliberately small."""

    name: str
    label: str
    default_model: str

    def configured(self) -> bool:
        """Whether credentials exist. Never raises, never calls out."""

    def build(self, model: str) -> Caller:
        """Return a callable for this model. Raises ProviderNotConfigured."""


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------


class GeminiProvider:
    name = "gemini"
    label = "Google Gemini"
    default_model = "gemini-2.5-flash"

    #: What the free tier allows per day, per project. Not enforced here — the
    #: provider is not the right place to refuse — but reported so the admin
    #: screen can warn before a run rather than after.
    free_tier_daily_requests = 20

    def configured(self) -> bool:
        return bool(settings.gemini_api_key)

    def models(self) -> list[str]:
        return ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]

    def build(self, model: str) -> Caller:
        if not self.configured():
            raise ProviderNotConfigured(
                "GEMINI_API_KEY is not set, so no Gemini model can be reached."
            )
        # Imported here rather than at module scope: importing this package must
        # not require every provider's SDK to be installed.
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)

        def _call(system_prompt: str, user_prompt: str, schema: Any = None) -> Any:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=schema,
                max_output_tokens=32000,
            )
            response = client.models.generate_content(
                model=model, contents=user_prompt, config=config
            )
            import json

            return json.loads(response.text or "null")

        return _call


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------


class AnthropicProvider:
    """Claude. Present so the seam is real rather than asserted.

    Left unconfigured until somebody sets a key: `configured()` returns False,
    the admin screen shows it as available-but-not-configured, and routing to it
    fails with a message naming the missing variable rather than a stack trace
    from an import.
    """

    name = "anthropic"
    label = "Anthropic Claude"
    default_model = "claude-sonnet-4-5"

    def configured(self) -> bool:
        return bool(getattr(settings, "anthropic_api_key", ""))

    def models(self) -> list[str]:
        return ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"]

    def build(self, model: str) -> Caller:
        if not self.configured():
            raise ProviderNotConfigured(
                "ANTHROPIC_API_KEY is not set, so no Claude model can be reached."
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ProviderNotConfigured(
                "The `anthropic` package is not installed. Add it to pyproject "
                "before routing a purpose to Claude."
            ) from exc

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        def _call(system_prompt: str, user_prompt: str, schema: Any = None) -> Any:
            # The schema is expressed in the prompt rather than as a response
            # format: the two providers disagree about structured output, and
            # the callers here already validate what comes back because a model
            # that returns the wrong shape is a case they must handle anyway.
            import json

            instruction = system_prompt
            if schema is not None:
                instruction += ("\n\nReply with JSON only, matching this schema:\n"
                                + json.dumps(schema))
            message = client.messages.create(
                model=model,
                max_tokens=8000,
                system=instruction,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
            return json.loads(text or "null")

        return _call


_PROVIDERS: dict[str, Any] = {
    GeminiProvider.name: GeminiProvider(),
    AnthropicProvider.name: AnthropicProvider(),
}


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def _configured_route(purpose: str) -> tuple[str, str]:
    """Which provider and model serve this purpose.

    Read from settings per purpose, falling back to the global default, and
    finally to Gemini — which is what every purpose used before this package
    existed, so an unconfigured deployment behaves exactly as it did.
    """
    per_purpose = (getattr(settings, "llm_routing", None) or {}).get(purpose)
    if per_purpose:
        provider, _, model = per_purpose.partition(":")
        if provider in _PROVIDERS:
            return provider, model or _PROVIDERS[provider].default_model

    return GeminiProvider.name, settings.gemini_model or GeminiProvider.default_model


def resolve(purpose: str) -> tuple[str, str]:
    """The provider name and model for a purpose, without building anything."""
    if purpose not in PURPOSES:
        raise LLMError(f"'{purpose}' is not a known purpose. "
                       f"Known: {', '.join(sorted(PURPOSES))}.")
    return _configured_route(purpose)


def caller_for(purpose: str) -> Caller:
    """A callable for this purpose, with usage accounted around it.

    Every call through here is counted, whether it succeeds or not. Google
    charges the allowance for a rejected request the same as a served one, so a
    counter that only records successes reports a day as unused on the day it
    ran out — which is exactly what happened.
    """
    provider_name, model = resolve(purpose)
    provider = _PROVIDERS[provider_name]
    inner = provider.build(model)

    def _counted(system_prompt: str, user_prompt: str, schema: Any = None) -> Any:
        from llm.usage import record

        try:
            result = inner(system_prompt, user_prompt, schema)
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            record(purpose, provider_name, model, ok=False, note=type(exc).__name__)
            message = str(exc).lower()
            if any(w in message for w in ("quota", "429", "resource_exhausted", "rate")):
                raise QuotaExhausted(str(exc)) from exc
            raise
        record(purpose, provider_name, model, ok=True)
        return result

    log.info("llm: %s -> %s/%s", purpose, provider_name, model)
    return _counted


def available_providers() -> list[dict[str, Any]]:
    """Every provider, whether it is usable, and what it offers.

    For the admin screen: a provider with no key is shown as present but
    unconfigured rather than hidden, so somebody wondering why Claude is not an
    option can see that the answer is a missing variable.
    """
    out = []
    for p in _PROVIDERS.values():
        out.append({
            "name": p.name,
            "label": p.label,
            "configured": p.configured(),
            "defaultModel": p.default_model,
            "models": p.models(),
        })
    return out


def describe_routing() -> list[dict[str, Any]]:
    """What each purpose currently resolves to, for the admin screen."""
    out = []
    for purpose in PURPOSES.values():
        provider, model = resolve(purpose.name)
        out.append({
            "purpose": purpose.name,
            "label": purpose.label,
            "needs": purpose.needs,
            "callsPerRun": purpose.calls_per_run,
            "provider": provider,
            "model": model,
            "configured": _PROVIDERS[provider].configured(),
        })
    return out
