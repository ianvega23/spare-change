"""Claude proposes, code decides.

The allocator asks the model for a target mix (JSON schema enforced by the API), then plain code
checks the shape, every guardrail in `spare.core.guard`, and the explanations. A failure is sent
back once with the errors; a second failure means no change. A passing mix is classified as
none / small / big, and the brain job applies small changes and turns big ones into a proposal
that waits for your Approve.

What the model sees: your three profile sentences (wrapped as data), current weights, the allowlist
with one-line descriptions, and the limits. Never transactions, merchants, account ids or prices.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..core import config as config_mod
from ..core import guard
from .llm import LLMClient, LLMError, LLMResult

PROMPT_VERSION = "allocator-v1"
PROMPT_PATH = Path(__file__).with_name("prompts") / f"{PROMPT_VERSION}.md"
MAX_ATTEMPTS = 2           # the first try, plus one retry with the errors
MAX_TARGETS = 20
MAX_REASON_CHARS = 300
MAX_SUMMARY_CHARS = 1500
MAX_PROFILE_CHARS = 300
MAX_TOKENS = 16000
CONFIDENCE = ("low", "medium", "high")

# Two or more capitals: single-letter tickers (A, C, F, T...) can't be told apart from words.
_SYMBOL_RE = re.compile(r"\b[A-Z][A-Z.]{1,5}\b")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class Outcome:
    status: str                                    # none | small | big | failed
    targets: dict[str, float] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    confidence: str = ""
    errors: list[str] = field(default_factory=list)
    calls: list[LLMResult] = field(default_factory=list)
    prompt_version: str = PROMPT_VERSION

    @property
    def model(self) -> str:
        return self.calls[-1].model if self.calls else ""


def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def allowed_funds(limits: guard.Limits, universe: guard.Universe,
                  etf_path: str | Path = config_mod.DEFAULT_ALLOWLIST) -> list[dict]:
    """The allowlist as the model sees it: symbol, class, expense ratio, one line. Stocks only if opted in."""
    with open(etf_path, "rb") as f:
        etfs = tomllib.load(f)
    funds = [{"symbol": s, "kind": "ETF", "asset_class": etfs.get(s, {}).get("asset_class", ""),
              "expense_ratio": etfs.get(s, {}).get("expense_ratio"),
              "description": etfs.get(s, {}).get("description", "")} for s in sorted(universe.etfs)]
    if limits.allow_stocks:
        funds += [{"symbol": s, "kind": "stock", "asset_class": "us_equity", "expense_ratio": 0,
                   "description": "S&P 500 company"} for s in sorted(universe.stocks)]
    return funds


def schema(symbols: list[str]) -> dict:
    """Enforced by the API. Numeric limits aren't expressible there, so the guard checks those."""
    return {
        "type": "object",
        "properties": {
            "targets": {"type": "array", "items": {
                "type": "object",
                "properties": {"symbol": {"type": "string", "enum": symbols},
                               "weight_percent": {"type": "integer"},
                               "reason": {"type": "string"}},
                "required": ["symbol", "weight_percent", "reason"],
                "additionalProperties": False}},
            "summary": {"type": "string"},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        },
        "required": ["targets", "summary", "confidence"],
        "additionalProperties": False,
    }


def _as_data(text: str) -> str:
    """Profile text can't close or open tags, so it can't pose as instructions."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def user_message(profile: dict, current: dict[str, float], holdings_cents: dict[str, int],
                 funds: list[dict], limits: guard.Limits, feedback: list[str] | None = None) -> str:
    lines = ["<profile_data>"]
    for key in ("goals", "risk", "horizon"):
        lines.append(f"{key}: {_as_data(str(profile.get(key, ''))[:MAX_PROFILE_CHARS])}")
    lines += ["</profile_data>", ""]
    if current:
        lines.append("Current target mix: " + ", ".join(
            f"{s} {round(w * 100)}%" for s, w in sorted(current.items(), key=lambda kv: -kv[1])))
    else:
        lines.append("Current target mix: none yet (this is the first allocation).")
    total = sum(holdings_cents.values())
    if total:
        lines.append("Holdings now, by share of money invested: " + ", ".join(
            f"{s} {round(c * 100 / total)}%" for s, c in sorted(holdings_cents.items(), key=lambda kv: -kv[1])))
    lines += ["", "Limits:",
              f"- at least {limits.min_holdings} holdings",
              f"- each ETF at most {round(limits.max_etf_weight * 100)}%"]
    if limits.allow_stocks:
        lines += [f"- each stock at most {round(limits.max_stock_weight * 100)}%",
                  f"- all stocks together at most {round(limits.max_stocks_total * 100)}%"]
    else:
        lines.append("- funds only: individual stocks are not enabled")
    lines += ["", "Allowed list (symbol | kind | asset class | expense ratio | description):"]
    for f in funds:
        ratio = f"{f['expense_ratio'] * 100:.2f}%" if isinstance(f["expense_ratio"], (int, float)) else "?"
        lines.append(f"{f['symbol']} | {f['kind']} | {f['asset_class']} | {ratio} | {f['description']}")
    if feedback:
        lines += ["", "Your previous answer was rejected by the checks for these reasons. Fix every one:"]
        lines += [f"- {e}" for e in feedback[:15]]
    return "\n".join(lines)


def _numbers(text: str) -> set[str]:
    out = set()
    for n in _NUMBER_RE.findall(text):
        out.add(n)
        if "." in n:
            out.add(n.rstrip("0").rstrip("."))
    return out


def check_explanations(targets: dict[str, float], reasons: dict[str, str], summary: str,
                       known_symbols: frozenset[str], input_text: str) -> list[str]:
    """Reasons and the summary may name only symbols in the mix and numbers from the inputs."""
    errors = []
    allowed_numbers = _numbers(input_text) | {str(round(w * 100)) for w in targets.values()} | {"100"}
    for where, text in [*((f"{s} reason", r) for s, r in reasons.items()), ("summary", summary)]:
        strays = sorted({m for m in _SYMBOL_RE.findall(text) if m in known_symbols and m not in targets})
        if strays:
            errors.append(f"{where} mentions {', '.join(strays)}, which is not in the mix")
        invented = sorted(_numbers(text) - allowed_numbers)
        if invented:
            errors.append(f"{where} uses numbers not in the inputs: {', '.join(invented[:5])}")
    return errors


def offered(universe: guard.Universe, limits: guard.Limits) -> frozenset[str]:
    """The symbols the model was shown: ETFs, plus stocks only when they're enabled."""
    return universe.etfs | universe.stocks if limits.allow_stocks else universe.etfs


def check(data, universe: guard.Universe, limits: guard.Limits, input_text: str):
    """Shape, size, guardrails and explanations. Returns (targets, reasons, summary, confidence, errors)."""
    if not isinstance(data, dict):
        return {}, {}, "", "", ["the answer is not a JSON object"]
    errors = []
    items = data.get("targets")
    summary = data.get("summary")
    confidence = data.get("confidence")
    if not isinstance(items, list) or not items:
        return {}, {}, "", "", ["targets must be a non-empty list"]
    if len(items) > MAX_TARGETS:
        return {}, {}, "", "", [f"too many targets ({len(items)}; at most {MAX_TARGETS})"]
    if not isinstance(summary, str) or not summary.strip():
        errors.append("summary must be a non-empty string")
        summary = ""
    elif len(summary) > MAX_SUMMARY_CHARS:
        errors.append(f"summary is too long ({len(summary)} characters; at most {MAX_SUMMARY_CHARS})")
    if confidence not in CONFIDENCE:
        errors.append("confidence must be low, medium or high")
        confidence = ""

    targets: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict):
            errors.append(f"target {n} is not an object")
            continue
        symbol, pct, reason = item.get("symbol"), item.get("weight_percent"), item.get("reason")
        if not isinstance(symbol, str) or not symbol:
            errors.append(f"target {n} has no symbol")
            continue
        symbol = symbol[:12]
        if symbol in targets:
            errors.append(f"{symbol} appears more than once")
            continue
        if not isinstance(pct, int) or isinstance(pct, bool):
            errors.append(f"{symbol}: weight_percent must be a whole number")
            continue
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{symbol}: needs a reason")
            reason = ""
        elif len(reason) > MAX_REASON_CHARS:
            errors.append(f"{symbol}: reason is too long ({len(reason)} characters; at most {MAX_REASON_CHARS})")
        targets[symbol] = pct / 100
        reasons[symbol] = reason.strip()

    if targets:
        errors += guard.validate_targets(targets, universe, limits)
    if not errors:
        errors += check_explanations(targets, reasons, summary, offered(universe, limits), input_text)
    return targets, reasons, summary.strip(), confidence, errors


def propose(llm: LLMClient, *, profile: dict, current: dict[str, float],
            holdings_cents: dict[str, int], universe: guard.Universe, limits: guard.Limits,
            funds: list[dict] | None = None) -> Outcome:
    funds = funds if funds is not None else allowed_funds(limits, universe)
    symbols = [f["symbol"] for f in funds]
    system = system_prompt()
    outcome = Outcome(status="failed")
    feedback: list[str] | None = None
    for _ in range(MAX_ATTEMPTS):
        user = user_message(profile, current, holdings_cents, funds, limits, feedback)
        try:
            result = llm.structured(system=system, user=user, schema=schema(symbols), max_tokens=MAX_TOKENS)
        except LLMError as e:
            partial = getattr(e, "partial", None)
            if partial is not None:
                outcome.calls.append(partial)
            if partial is not None and partial.data is None and "JSON" in str(e):
                feedback = ["the answer was not valid JSON matching the schema"]
                outcome.errors = feedback
                continue
            outcome.errors = [str(e)]
            return outcome
        outcome.calls.append(result)
        targets, reasons, summary, confidence, errors = check(result.data, universe, limits, user)
        if errors:
            outcome.errors = errors
            feedback = errors
            continue
        status = guard.classify(current, targets)
        return Outcome(status=status, targets=targets, reasons=reasons, summary=summary,
                       confidence=confidence, calls=outcome.calls)
    return outcome
