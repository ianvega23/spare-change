"""The Sunday update: about six lines, every number computed by code.

`facts()` gathers the week from the state branch. `template()` turns them into the message with no
AI at all. `draft()` may ask the cheap model to word it more warmly, but the draft is used only if
every number in it already appears in the template, and it is short; otherwise the template goes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..core import config as config_mod
from ..core import proposals
from ..core import shadow
from ..core.state import State
from . import categorize
from .llm import LLMClient, LLMError, LLMResult

PROMPT_VERSION = "summary-v1"
STALE_RUN_DAYS = 3
VALUATION_MAX_AGE_DAYS = 7
MAX_DRAFT_CHARS = 900
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _day(d: date) -> str:
    return f"{d.strftime('%a')} {d.strftime('%b')} {d.day}"


@dataclass
class Facts:
    week_start: date
    week_end: date
    purchases: int = 0
    roundups_cents: int = 0
    multiplier: int = 1
    bought_cents: int = 0
    bought_funds: int = 0
    buy_day: str | None = None
    pot_cents: int = 0
    holdings_cost_cents: int = 0
    holdings_funds: int = 0
    holdings_value_cents: int | None = None
    shadow_value_cents: int | None = None
    shadow_invested_cents: int = 0
    categories: dict[str, int] = field(default_factory=dict)   # category -> cents spent
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_buy: str = "Monday"


def facts(state: State, today: date, categories: dict[str, str] | None = None) -> Facts:
    """The week ending `today` (normally a Sunday): Monday to today."""
    cfg = config_mod.load(state.config_path) if state.config_path.exists() else config_mod.from_dict({})
    start = today - timedelta(days=today.weekday())
    ledger = state.ledger()
    in_week = lambda d: start.isoformat() <= str(d) <= today.isoformat()  # noqa: E731
    f = Facts(week_start=start, week_end=today, multiplier=cfg.multiplier,
              next_buy=config_mod.WEEKDAYS[cfg.buy_weekday].capitalize())

    roundups = [e for e in ledger.of_type("roundup") if in_week(e.get("date"))]
    f.purchases = len(roundups)
    f.roundups_cents = sum(int(e["cents"]) for e in roundups)
    if categories is not None:
        for e in roundups:
            c = categories.get(categorize.key(e.get("merchant", "")), "other")
            f.categories[c] = f.categories.get(c, 0) + int(e.get("amount_cents", 0))

    buys = [b for b in ledger.buys() if in_week(b.get("date"))]
    f.bought_cents = sum(int(b["notional_cents"]) for b in buys)
    f.bought_funds = len({b["symbol"] for b in buys})
    f.buy_day = max((b["date"] for b in buys), default=None)
    f.pot_cents = ledger.pot_cents()
    holdings = ledger.holdings_cents()
    f.holdings_cost_cents = sum(holdings.values())
    f.holdings_funds = len(holdings)

    valuation = state._read_json("valuation.json") or {}
    fresh = isinstance(valuation, dict) and valuation.get("day") and \
        (today - date.fromisoformat(valuation["day"])).days <= VALUATION_MAX_AGE_DAYS
    if fresh:
        positions = valuation.get("positions_cents") or {}
        if positions:
            f.holdings_value_cents = sum(int(v) for v in positions.values())
        lines = state.read_jsonl("shadow.jsonl")
        f.shadow_value_cents = shadow.value_cents(lines, valuation.get("vti_price"))
        f.shadow_invested_cents = shadow.invested_cents(lines)

    for d in state.read_jsonl("decisions.jsonl"):
        if not in_week(str(d.get("at", ""))[:10]):
            continue
        if d.get("type") == "targets_changed":
            f.changes.append("a small allocation change was applied")
        elif d.get("status") in ("approved", "skipped", "expired", "rejected"):
            f.changes.append(f"you {d['status']} a proposal" if d["status"] in ("approved", "skipped")
                             else f"a proposal was {d['status']}")
    waiting = proposals.pending(state)
    if waiting:
        f.changes.append(f"{len(waiting)} proposal{'s' if len(waiting) > 1 else ''} waiting for your answer")

    last = state.last_run()
    if not last:
        f.warnings.append("the invest job hasn't run yet")
    else:
        age = (today - date.fromisoformat(last["day"])).days
        if age > STALE_RUN_DAYS:
            f.warnings.append(f"the invest job last ran {age} days ago; check that GitHub Actions is still on")
        elif not last.get("ok"):
            f.warnings.append(f"the last invest run failed ({last.get('status')})")
    if state.paused() or not cfg.enabled:
        f.warnings.append("paused: send /resume to start buying again")
    return f


def template(f: Facts) -> str:
    lines = [f"This week ({_day(f.week_start)} to {_day(f.week_end)}): {f.purchases} purchases, "
             f"{usd(f.roundups_cents)} in round-ups ({f.multiplier}x)."]
    if f.bought_cents:
        day = date.fromisoformat(f.buy_day).strftime("%A") if f.buy_day else "this week"
        lines.append(f"Invested {day}: {usd(f.bought_cents)} across {f.bought_funds} funds. "
                     f"Pot now {usd(f.pot_cents)}.")
    else:
        lines.append(f"No buy this week. Pot {usd(f.pot_cents)}.")
    if f.holdings_value_cents is not None:
        holding = f"Holdings: {usd(f.holdings_value_cents)} across {f.holdings_funds} funds"
    else:
        holding = f"Holdings: {usd(f.holdings_cost_cents)} at cost across {f.holdings_funds} funds"
    if f.shadow_value_cents is not None:
        holding += f". Shadow all-VTI portfolio: {usd(f.shadow_value_cents)}"
    lines.append(holding + ".")
    top = sorted(((c, v) for c, v in f.categories.items() if v > 0), key=lambda kv: -kv[1])[:3]
    if top:
        lines.append("Spending: " + ", ".join(f"{c} {usd(v)}" for c, v in top) + ".")
    lines.append(("Changes: " + "; ".join(f.changes) + ".") if f.changes else "No changes this week.")
    lines.append(f"Next buy {f.next_buy}.")
    lines += [f"Heads up: {w}." for w in f.warnings]
    return "\n".join(lines)


SYSTEM = (
    "You rewrite a short weekly money update so it reads warmly and clearly, in at most six short lines. "
    "Keep every fact. Use only the numbers, dates and fund counts that appear in the update; do not add, "
    "round, total or change any number. No advice, no predictions, no emojis."
)
SCHEMA = {"type": "object", "properties": {"message": {"type": "string"}},
          "required": ["message"], "additionalProperties": False}


def numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def check_draft(draft: str, base: str) -> list[str]:
    errors = []
    if not draft.strip():
        errors.append("empty draft")
    if len(draft) > MAX_DRAFT_CHARS:
        errors.append("draft too long")
    invented = numbers(draft) - numbers(base)
    if invented:
        errors.append("draft has numbers not in the facts: " + ", ".join(sorted(invented)[:5]))
    dollars = {n for n in re.findall(r"\$[\d,]+\.\d\d", base)}
    missing = [d for d in dollars if d not in draft]
    if missing:
        errors.append("draft dropped amounts: " + ", ".join(sorted(missing)[:5]))
    return errors


def draft(base: str, llm: LLMClient | None) -> tuple[str, list[LLMResult], str]:
    """(message, model calls, 'ai' | 'template')."""
    if llm is None:
        return base, [], "template"
    try:
        result = llm.structured(system=SYSTEM, user=f"<update>\n{base}\n</update>", schema=SCHEMA,
                                max_tokens=2000)
    except LLMError as e:
        partial = getattr(e, "partial", None)
        return base, [partial] if partial else [], "template"
    text = result.data.get("message", "") if isinstance(result.data, dict) else ""
    if not isinstance(text, str) or check_draft(text, base):
        return base, [result], "template"
    return text.strip(), [result], "ai"
