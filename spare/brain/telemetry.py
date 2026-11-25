"""One line per model call in state/telemetry.jsonl: tokens, cost, latency, model and prompt version.

The monthly AI budget is checked against this file before every brain run. If the budget would be
passed, the run is skipped: targets stay as they are, and buying carries on as usual.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..core.state import State
from .llm import LLMResult

FILE = "telemetry.jsonl"
MICROCENTS_PER_CENT = 1_000_000


def record(state: State, result: LLMResult, *, purpose: str, prompt_version: str,
           outcome: str = "", now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    line = {"at": now.isoformat(timespec="seconds"), "purpose": purpose, "model": result.model,
            "prompt_version": prompt_version, "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens, "cache_read_tokens": result.cache_read_tokens,
            "cache_write_tokens": result.cache_write_tokens, "latency_ms": result.latency_ms,
            "cost_microcents": result.cost_microcents, "stop_reason": result.stop_reason,
            "outcome": outcome}
    with open(state.root / FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")
    return line


def calls(state: State) -> list[dict]:
    path = state.root / FILE
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue  # a torn line never blocks the budget check; it just isn't counted
    return out


def month_spend_microcents(state: State, month: str) -> int:
    """`month` is 'YYYY-MM'."""
    return sum(int(c.get("cost_microcents", 0)) for c in calls(state)
               if str(c.get("at", "")).startswith(month))


def within_budget(state: State, budget_cents: int, estimate_microcents: int,
                  now: datetime | None = None) -> tuple[bool, int]:
    """(allowed, spent so far this month in micro-cents). Allowed only if the estimate still fits."""
    now = now or datetime.now(timezone.utc)
    spent = month_spend_microcents(state, now.strftime("%Y-%m"))
    return spent + estimate_microcents <= budget_cents * MICROCENTS_PER_CENT, spent


def usd(microcents: int) -> str:
    return f"${microcents / MICROCENTS_PER_CENT / 100:,.4f}"
