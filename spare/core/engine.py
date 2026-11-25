"""Round-ups, multiplier, pot, weekly buy and order split. Pure functions, integer cents."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

MIN_ORDER_CENTS = 100  # Alpaca's minimum notional order is $1


@dataclass(frozen=True)
class Transaction:
    id: str
    date: str  # ISO date, YYYY-MM-DD
    description: str
    amount_cents: int  # positive = money spent; credits and refunds are negative or zero
    posted: bool = True


def round_up_cents(amount_cents: int) -> int:
    """Spare change for one purchase: $4.25 -> 75, $5.00 -> 0, $10.01 -> 99, credits -> 0."""
    if amount_cents <= 0:
        return 0
    return (-amount_cents) % 100


def roundup_for(txn: Transaction, multiplier: int) -> int:
    """Round-up for a transaction after the multiplier. Pending charges count only once posted."""
    if not txn.posted:
        return 0
    return round_up_cents(txn.amount_cents) * multiplier


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")


def is_buy_day(day: date, buy_weekday: int, last_buy: date | None) -> bool:
    """Buy once a week: on the buy weekday, or on a later weekday of the same week if the market
    was closed (a holiday), as long as this week hasn't had a buy yet."""
    if day.weekday() > 4 or day.weekday() < buy_weekday:
        return False
    return last_buy is None or last_buy.isocalendar()[:2] != day.isocalendar()[:2]


def buy_amount(pot_cents: int, monthly_cap_cents: int, invested_this_month_cents: int) -> int:
    """How much to buy on a buy day: the whole pot (this week's round-ups plus anything carried
    over), clamped to what's left of the monthly cap. Under $1 waits for next week."""
    room = max(0, monthly_cap_cents - invested_this_month_cents)
    amount = min(pot_cents, room)
    return amount if amount >= MIN_ORDER_CENTS else 0


def split(amount_cents: int, targets: dict[str, float],
          holdings_cents: dict[str, int] | None = None) -> dict[str, int]:
    """Split a buy across the target allocation without selling anything.

    Each holding's gap is how far it would sit below its target after this buy. The buy is shared
    in proportion to those gaps, so new money steers the portfolio toward the target. With no
    holdings (or a portfolio already on target) that is simply proportional to the weights.
    Slices under $1 are dropped and given to the most underweight holding. Returns {} if the
    amount can't make even one $1 order. The slices always add up to `amount_cents`.
    """
    if amount_cents < MIN_ORDER_CENTS or not targets:
        return {}
    pct = {s: round(w * 100) for s, w in targets.items()}  # guard ensures whole percents
    holdings = {s: (holdings_cents or {}).get(s, 0) for s in targets}
    after = sum(holdings.values()) + amount_cents
    # All integer arithmetic, in units of cent-percent.
    gaps = {s: max(0, pct[s] * after - 100 * holdings[s]) for s in targets}
    total_gap = sum(gaps.values())
    if total_gap <= 0:
        gaps, total_gap = dict(pct), sum(pct.values())

    # Largest-remainder rounding so the slices sum exactly to the amount.
    slices = {s: amount_cents * gaps[s] // total_gap for s in targets}
    remainders = {s: amount_cents * gaps[s] % total_gap for s in targets}
    leftover = amount_cents - sum(slices.values())
    for s in sorted(targets, key=lambda s: (-remainders[s], s))[:leftover]:
        slices[s] += 1

    # Fold any slice under $1 into the most underweight holding that remains.
    while True:
        small = [s for s, c in slices.items() if 0 < c < MIN_ORDER_CENTS]
        if not small:
            break
        smallest = min(small, key=lambda s: (slices[s], s))
        others = [s for s, c in slices.items() if c > 0 and s != smallest]
        if not others:
            return {}
        receiver = max(others, key=lambda s: (gaps[s], slices[s], s))
        slices[receiver] += slices.pop(smallest)
    return {s: c for s, c in slices.items() if c > 0}


def batch_id(previous_batch_id: str, roundup_ids: list[str]) -> str:
    """Deterministic id for one pot: the round-ups it spends, chained to the previous batch."""
    payload = previous_batch_id + "|" + ",".join(sorted(roundup_ids))
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def client_order_id(batch: str, symbol: str) -> str:
    """Idempotency key for one order. A retried run makes the same id, and the broker rejects it."""
    return "spare-" + hashlib.sha256((batch + symbol).encode()).hexdigest()[:24]
