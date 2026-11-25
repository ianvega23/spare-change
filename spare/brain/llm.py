"""The model adapter. Everything above this file talks to `LLMClient`, never to a provider SDK.

`ClaudeClient` uses the official `anthropic` SDK with structured outputs, so the API itself enforces
the JSON schema. `FakeClient` returns canned replies (valid, broken, adversarial) for tests and evals.
Adding another provider means one new class with the same `structured` method.

Cost is tracked in integer micro-cents (a millionth of a cent). Prices are in cents per million
tokens, so tokens x price is micro-cents exactly, with no floats.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

# Cents per million tokens (input, output), from Anthropic's price list. Unknown models are billed
# at the most expensive known rate, so the budget errs on the safe side.
PRICES_CENTS_PER_MTOK = {
    "claude-opus-5-5": (400, 2000),
    "claude-sonnet-5-5": (200, 1000),
    "claude-haiku-5-5": (10, 50),
    "claude-opus-4-8": (500, 2500),
    "claude-sonnet-5": (200, 1000),
}
FALLBACK_PRICE = (max(p[0] for p in PRICES_CENTS_PER_MTOK.values()),
                  max(p[1] for p in PRICES_CENTS_PER_MTOK.values()))
# Server-side refusal fallbacks exist for these model families (not Haiku).
FALLBACK_FAMILIES = ("claude-opus-", "claude-sonnet-", "claude-fable-")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """The model call failed or gave no usable answer. The brain job keeps the current targets."""


@dataclass
class LLMResult:
    data: Any                      # the parsed JSON (shape not yet checked: that's the caller's job)
    model: str                     # the model that actually answered (a fallback may differ)
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_microcents: int
    stop_reason: str = "end_turn"
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def cost_microcents(model: str, input_tokens: int, output_tokens: int,
                    cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> int:
    price_in, price_out = PRICES_CENTS_PER_MTOK.get(model, FALLBACK_PRICE)
    # Cache reads bill at 10% of input, cache writes at 125%. Rounded up, never down.
    return (input_tokens * price_in + output_tokens * price_out
            + -(-cache_read_tokens * price_in // 10) + -(-cache_write_tokens * price_in * 5 // 4))


def estimate_microcents(model: str, input_tokens: int, output_tokens: int) -> int:
    """A pre-call estimate for the budget check."""
    return cost_microcents(model, input_tokens, output_tokens)


class LLMClient(Protocol):
    model: str

    def structured(self, *, system: str, user: str, schema: dict, max_tokens: int) -> LLMResult: ...


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class ClaudeClient:
    """Claude through the official SDK. The SDK retries 429s, 5xx and dropped connections itself."""

    def __init__(self, model: str, *, api_key: str | None = None, effort: str = "high",
                 client=None, error_types: tuple[type[BaseException], ...] | None = None,
                 clock: Callable[[], float] = time.monotonic, timeout: float = 120.0):
        self.model = model
        self.effort = effort
        self._clock = clock
        if client is None:
            import anthropic  # only the brain job installs it (requirements-ai.txt)
            client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=timeout)
            error_types = (anthropic.APIError,)
        self._client = client
        self._errors = error_types or ()

    def __repr__(self) -> str:  # never show the key
        return f"ClaudeClient(model={self.model!r})"

    def structured(self, *, system: str, user: str, schema: dict, max_tokens: int = 16000) -> LLMResult:
        params: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": self.effort,
                              "format": {"type": "json_schema", "schema": schema}},
        }
        if self.model.startswith(FALLBACK_FAMILIES):
            # On a safety decline the API re-runs the request on a fallback model in the same call.
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        start = self._clock()
        try:
            response = self._client.beta.messages.create(**params)
        except self._errors as e:  # type: ignore[misc]
            raise LLMError(f"the Claude API call failed ({type(e).__name__}: {str(e)[:200]})") from None
        latency_ms = int((self._clock() - start) * 1000)

        usage = getattr(response, "usage", None)
        tokens_in = _int(getattr(usage, "input_tokens", 0))
        tokens_out = _int(getattr(usage, "output_tokens", 0))
        cache_read = _int(getattr(usage, "cache_read_input_tokens", 0))
        cache_write = _int(getattr(usage, "cache_creation_input_tokens", 0))
        model = getattr(response, "model", None) or self.model
        cost = cost_microcents(model, tokens_in, tokens_out, cache_read, cache_write)
        stop = getattr(response, "stop_reason", None) or "unknown"

        def fail(message: str):
            err = LLMError(message)
            err.partial = LLMResult(None, model, tokens_in, tokens_out, latency_ms, cost, stop,
                                    cache_read, cache_write)  # so the spend is still recorded
            raise err

        if stop == "refusal":
            fail("Claude declined the request")
        if stop == "max_tokens":
            fail("Claude's answer was cut off at max_tokens")
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), None)
        if text is None:
            fail("Claude's answer had no text")
        try:
            data = json.loads(text)
        except ValueError:
            fail("Claude's answer was not valid JSON")
        return LLMResult(data, model, tokens_in, tokens_out, latency_ms, cost, stop, cache_read, cache_write)


@dataclass
class FakeClient:
    """Replays canned answers in order. Each reply is a dict/list (parsed JSON), a str (raw text that
    may be broken JSON), or an exception to raise. Records every request for assertions."""

    replies: list
    model: str = "fake-model"
    input_tokens: int = 3000
    output_tokens: int = 800
    calls: list[dict] = field(default_factory=list)

    def structured(self, *, system: str, user: str, schema: dict, max_tokens: int = 16000) -> LLMResult:
        self.calls.append({"system": system, "user": user, "schema": schema, "max_tokens": max_tokens})
        if not self.replies:
            raise LLMError("the fake client ran out of replies")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, str):
            try:
                reply = json.loads(reply)
            except ValueError:
                err = LLMError("the answer was not valid JSON")
                err.partial = self._result(None)
                raise err from None
        return self._result(reply)

    def _result(self, data) -> LLMResult:
        return LLMResult(data, self.model, self.input_tokens, self.output_tokens, 5,
                         cost_microcents(self.model, self.input_tokens, self.output_tokens))
