"""List prices, for estimating what model calls cost.

An estimate, and labelled as one everywhere it is shown:

* **List prices, paid tier.** The free Gemini tier is not billed at all, so a
  deployment living inside it pays nothing whatever this says. The figure is
  what the same usage would cost on a paid plan, which is the number worth
  knowing before switching a purpose to Claude.
* **Checked on `PRICES_AS_OF`.** Providers revise prices; nothing here fetches
  them. When they change, edit this table.
* **No published rate, no estimate.** A model missing from the table has its
  tokens counted and its cost left blank. A guessed rate would put a confident
  number on screen that nobody could trace.

Rates are US dollars per million tokens.
"""
from __future__ import annotations

from dataclasses import dataclass

PRICES_AS_OF = "2026-09-15"

#: Where to check them again.
PRICE_SOURCES = {
    "anthropic": "https://docs.anthropic.com/en/docs/about-claude/pricing",
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
}


@dataclass(frozen=True)
class Rate:
    input: float
    output: float
    #: Cached input read back. Claude bills it at a tenth of the input rate.
    cache_read: float
    #: Input written to the cache. Claude bills a five-minute write at 1.25×;
    #: Gemini charges cache storage by the hour instead, which is not a per-call
    #: cost and is not estimated here.
    cache_write: float
    #: Gemini 2.5 Pro doubles its rates once a prompt passes 200k tokens.
    long_prompt_threshold: int | None = None
    long_input: float | None = None
    long_output: float | None = None


def _claude(inp: float, out: float, cache_read: float | None = None) -> Rate:
    return Rate(input=inp, output=out,
                cache_read=cache_read if cache_read is not None else round(inp * 0.1, 4),
                cache_write=round(inp * 1.25, 4))


RATES: dict[tuple[str, str], Rate] = {
    ("anthropic", "claude-fable-5-1"): _claude(10.00, 50.00, cache_read=0.25),
    ("anthropic", "claude-fable-5"): _claude(10.00, 50.00),
    ("anthropic", "claude-opus-5"): _claude(5.00, 25.00),
    ("anthropic", "claude-opus-4-8"): _claude(5.00, 25.00),
    ("anthropic", "claude-opus-4-7"): _claude(5.00, 25.00),
    ("anthropic", "claude-opus-4-6"): _claude(5.00, 25.00),
    ("anthropic", "claude-sonnet-5"): _claude(2.00, 10.00),
    ("anthropic", "claude-sonnet-4-6"): _claude(3.00, 15.00),
    ("anthropic", "claude-haiku-4-5"): _claude(1.00, 5.00),
    ("gemini", "gemini-2.5-flash"): Rate(input=0.30, output=2.50, cache_read=0.03, cache_write=0.0),
    ("gemini", "gemini-2.5-pro"): Rate(input=1.25, output=10.00, cache_read=0.125, cache_write=0.0,
                                       long_prompt_threshold=200_000,
                                       long_input=2.50, long_output=15.00),
}


def rate_for(provider: str, model: str) -> Rate | None:
    """The rate for a model, or None when there is no published one.

    Exact match first, then the longest known ID the model name starts with, so
    a dated snapshot ("claude-haiku-4-5-20251001") prices as its model.
    """
    provider = (provider or "").lower()
    model = (model or "").lower()
    exact = RATES.get((provider, model))
    if exact is not None:
        return exact
    candidates = [(m, r) for (p, m), r in RATES.items() if p == provider and model.startswith(m + "-")]
    if not candidates:
        return None
    return max(candidates, key=lambda c: len(c[0]))[1]


def estimate(provider: str, model: str, *, input_tokens: int = 0, output_tokens: int = 0,
             cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float | None:
    """Estimated US dollars for one call, or None without a published rate.

    `input_tokens` is uncached input only; cached reads and writes are passed
    separately so each is charged at its own rate.
    """
    rate = rate_for(provider, model)
    if rate is None:
        return None
    inp, out = rate.input, rate.output
    prompt = input_tokens + cache_read_tokens + cache_write_tokens
    if rate.long_prompt_threshold is not None and prompt > rate.long_prompt_threshold:
        inp = rate.long_input or inp
        out = rate.long_output or out
    return (input_tokens * inp + output_tokens * out
            + cache_read_tokens * rate.cache_read + cache_write_tokens * rate.cache_write) / 1_000_000


def rate_table() -> list[dict[str, object]]:
    """Every known rate, for the admin screen's reference list."""
    return [
        {"provider": p, "model": m, "input": r.input, "output": r.output,
         "cacheRead": r.cache_read, "cacheWrite": r.cache_write,
         "longPromptThreshold": r.long_prompt_threshold}
        for (p, m), r in RATES.items()
    ]
