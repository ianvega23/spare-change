"""The shadow index portfolio: every real buy mirrored, same day and dollars, into all-VTI.

Forward-only (no backtest). The invest job appends one line per buy to state/shadow.jsonl with
VTI's price at that moment; the weekly update values the shadow at today's VTI price. Decimal
math, rounded down to whole cents.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, InvalidOperation

SHADOW_SYMBOL = "VTI"


def entry(day: str, cents: int, price: str) -> dict:
    return {"date": day, "cents": cents, "symbol": SHADOW_SYMBOL, "price": price}


def shares(lines: list[dict]) -> Decimal:
    total = Decimal(0)
    for line in lines:
        try:
            price = Decimal(str(line["price"]))
            if price > 0:
                total += Decimal(int(line["cents"])) / 100 / price
        except (KeyError, ValueError, TypeError, InvalidOperation):
            continue  # a malformed line is skipped, never guessed at
    return total


def value_cents(lines: list[dict], price: str | None) -> int | None:
    if not lines or not price:
        return None
    try:
        value = shares(lines) * Decimal(str(price)) * 100
    except InvalidOperation:
        return None
    return int(value.to_integral_value(rounding=ROUND_DOWN))


def invested_cents(lines: list[dict]) -> int:
    return sum(int(line.get("cents", 0)) for line in lines)
