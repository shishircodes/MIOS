"""Which model answers which question.

Three call sites reach for a model today — classification, Market Pulse and
Mode Publish — and each imported `_build_gemini_caller` from
`agents.signal_analyst` directly. That worked while there was one provider, and
made a second one a change to every caller.

This package puts one seam in the way. A caller asks for a *purpose*:

    from llm import caller_for, PURPOSE_PUSH
    call = caller_for(PURPOSE_PUSH)
    result = call(system_prompt, user_prompt, schema)

and what answers is decided by configuration. Adding Anthropic means adding a
provider here; it does not mean touching the code that classifies signals or
ranks companies.

**Purposes, not models, at the call site.** Classification wants a cheap model
that returns strict JSON for a hundred records; a written summary wants a
stronger one; ranking wants something in between. Tying those to model names in
the modules that use them would mean the choice could only be revised by editing
them all. A purpose is stable; the model behind it is a setting.

**The daily quota is per provider.** The free Gemini tier allows twenty requests
a day across a project, which the pipeline has already exhausted once — with the
counter reading zero, because it only ever counted successful classification
calls and never Market Pulse or a failed attempt. Accounting lives here now, at
the seam every call passes through, so it cannot be sidestepped by a new caller
that forgets.

Nothing here changes behaviour today: with no configuration, every purpose
resolves to the same Gemini model as before.
"""
from __future__ import annotations

from llm.providers import (
    LLMError,
    ProviderNotConfigured,
    QuotaExhausted,
    available_providers,
    caller_for,
    describe_routing,
    resolve,
)
from llm.purposes import (
    PURPOSE_CLASSIFY,
    PURPOSE_PUBLISH,
    PURPOSE_PULSE,
    PURPOSE_PUSH,
    PURPOSES,
)

__all__ = [
    "LLMError",
    "PURPOSES",
    "PURPOSE_CLASSIFY",
    "PURPOSE_PUBLISH",
    "PURPOSE_PULSE",
    "PURPOSE_PUSH",
    "ProviderNotConfigured",
    "QuotaExhausted",
    "available_providers",
    "caller_for",
    "describe_routing",
    "resolve",
]
