"""Every guardrail, in plain code. Pure functions used by both the brain and invest jobs.

Each validator returns a list of human-readable errors; an empty list means "allowed".
Config can tighten these limits but never loosen them past the hard limits below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .engine import MIN_ORDER_CENTS

# Hard limits. Loosening any of these is a deliberate code change, visible in a pull request.
HARD_MIN_HOLDINGS = 5
HARD_MAX_ETF_WEIGHT = 0.35
HARD_MAX_STOCK_WEIGHT = 0.10
HARD_MAX_STOCKS_TOTAL = 0.30
SMALL_CHANGE_MAX_SHIFT = 0.10
AUTO_CHANGE_MIN_INTERVAL = timedelta(days=7)
SUM_TOLERANCE = 0.001
BUY_TRIGGER_RANGE_CENTS = (1_000, 50_000)
ALLOWED_MULTIPLIERS = (1, 2, 3)
DEFAULT_MONTHLY_CAP_CENTS = 20_000

_EPS = 1e-9


@dataclass(frozen=True)
class Limits:
    min_holdings: int = HARD_MIN_HOLDINGS
    max_etf_weight: float = HARD_MAX_ETF_WEIGHT
    max_stock_weight: float = HARD_MAX_STOCK_WEIGHT
    max_stocks_total: float = HARD_MAX_STOCKS_TOTAL
    allow_stocks: bool = False


@dataclass(frozen=True)
class Universe:
    etfs: frozenset[str]
    stocks: frozenset[str] = field(default_factory=frozenset)


def validate_limits(limits: Limits) -> list[str]:
    """Config may only make limits stricter."""
    errors = []
    if limits.min_holdings < HARD_MIN_HOLDINGS:
        errors.append(f"min_holdings {limits.min_holdings} is below the hard minimum of {HARD_MIN_HOLDINGS}")
    for name, value, hard in (("max_etf_weight", limits.max_etf_weight, HARD_MAX_ETF_WEIGHT),
                              ("max_stock_weight", limits.max_stock_weight, HARD_MAX_STOCK_WEIGHT),
                              ("max_stocks_total", limits.max_stocks_total, HARD_MAX_STOCKS_TOTAL)):
        if not 0 < value <= hard + _EPS:
            errors.append(f"{name} {value} must be above 0 and at most {hard}")
    return errors


def validate_settings(*, buy_trigger_cents: int, multiplier: int, monthly_cap_cents: int) -> list[str]:
    errors = []
    lo, hi = BUY_TRIGGER_RANGE_CENTS
    if not isinstance(buy_trigger_cents, int) or not lo <= buy_trigger_cents <= hi:
        errors.append(f"buy trigger must be between ${lo // 100} and ${hi // 100}")
    if multiplier not in ALLOWED_MULTIPLIERS:
        errors.append("multiplier must be 1, 2 or 3")
    if not isinstance(monthly_cap_cents, int) or monthly_cap_cents < MIN_ORDER_CENTS:
        errors.append("monthly cap must be at least $1")
    elif isinstance(buy_trigger_cents, int) and monthly_cap_cents < buy_trigger_cents:
        errors.append("monthly cap must be at least the buy trigger")
    return errors


def validate_targets(targets: dict[str, float], universe: Universe,
                     limits: Limits = Limits()) -> list[str]:
    """A target allocation: symbol -> weight (0.35 = 35%)."""
    errors = []
    if not isinstance(targets, dict) or not targets:
        return ["targets must be a non-empty mapping of symbol to weight"]
    stocks_total = 0.0
    for symbol, weight in targets.items():
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            errors.append(f"{symbol}: weight must be a number")
            continue
        if weight <= 0:
            errors.append(f"{symbol}: weight must be above 0 (no sells or shorts)")
            continue
        if abs(weight * 100 - round(weight * 100)) > 1e-6:
            errors.append(f"{symbol}: weight {weight} is not a whole percent")
        if symbol in universe.etfs:
            if weight > limits.max_etf_weight + _EPS:
                errors.append(f"{symbol}: {weight:.0%} is over the {limits.max_etf_weight:.0%} ETF cap")
        elif symbol in universe.stocks:
            if not limits.allow_stocks:
                errors.append(f"{symbol}: individual stocks are not enabled")
            elif weight > limits.max_stock_weight + _EPS:
                errors.append(f"{symbol}: {weight:.0%} is over the {limits.max_stock_weight:.0%} stock cap")
            stocks_total += weight
        else:
            errors.append(f"{symbol}: not in the allowlist")
    if len(targets) < limits.min_holdings:
        errors.append(f"needs at least {limits.min_holdings} holdings, got {len(targets)}")
    if stocks_total > limits.max_stocks_total + _EPS:
        errors.append(f"stocks total {stocks_total:.0%} is over the {limits.max_stocks_total:.0%} cap")
    numeric = [w for w in targets.values() if isinstance(w, (int, float)) and not isinstance(w, bool)]
    if abs(sum(numeric) - 1) > SUM_TOLERANCE:
        errors.append(f"weights sum to {sum(numeric):.2%}, not 100%")
    return errors


def shift(old: dict[str, float], new: dict[str, float]) -> float:
    """Total variation between two allocations: moving 5 points from VTI to VXUS is 0.05."""
    symbols = set(old) | set(new)
    return sum(abs(new.get(s, 0) - old.get(s, 0)) for s in symbols) / 2


def classify(old: dict[str, float], new: dict[str, float]) -> str:
    """'none', 'small' (applied on its own) or 'big' (needs Approve in Telegram).

    Big: the first allocation, adding or removing a holding, or a shift over 10 points.
    """
    if not old or set(old) != set(new):
        return "big"
    moved = shift(old, new)
    if moved < _EPS:
        return "none"
    return "small" if moved <= SMALL_CHANGE_MAX_SHIFT + _EPS else "big"


def auto_change_allowed(last_auto_change: datetime | None, now: datetime) -> bool:
    """At most one automatic small change per week, to stop churn."""
    return last_auto_change is None or now - last_auto_change >= AUTO_CHANGE_MIN_INTERVAL


def validate_buy(*, amount_cents: int, invested_this_month_cents: int,
                 monthly_cap_cents: int, cash_cents: int | None = None) -> list[str]:
    errors = []
    if amount_cents < MIN_ORDER_CENTS:
        errors.append("buy is under the $1 minimum")
    if invested_this_month_cents + amount_cents > monthly_cap_cents:
        errors.append("buy would pass the monthly cap")
    if cash_cents is not None and cash_cents < amount_cents:
        errors.append("not enough cash in the brokerage account")
    return errors


def validate_order_plan(slices: dict[str, int], targets: dict[str, float],
                        amount_cents: int) -> list[str]:
    """The exact orders about to be placed: buys only, $1 minimum, only target symbols, right total."""
    errors = []
    if not slices:
        errors.append("no orders to place")
    for symbol, cents in slices.items():
        if not isinstance(cents, int) or isinstance(cents, bool):
            errors.append(f"{symbol}: order amount must be whole cents")
            continue
        if cents < 0:
            errors.append(f"{symbol}: sells are blocked in v1")
        elif cents < MIN_ORDER_CENTS:
            errors.append(f"{symbol}: order under the $1 minimum")
        if symbol not in targets:
            errors.append(f"{symbol}: not in the approved targets")
    if sum(c for c in slices.values() if isinstance(c, int)) != amount_cents:
        errors.append("orders don't add up to the buy amount")
    return errors


def check_tradable(symbol: str, asset: dict | None) -> list[str]:
    """Live asset check from the broker: must be tradable and fractionable."""
    if not asset:
        return [f"{symbol}: unknown to the broker"]
    errors = []
    if not asset.get("tradable"):
        errors.append(f"{symbol}: not tradable")
    if not asset.get("fractionable"):
        errors.append(f"{symbol}: not fractionable")
    return errors
