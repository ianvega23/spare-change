"""One run of the money path (docs/PLAN.md section 3), with the network behind interfaces.

reconcile -> fetch -> round up -> weekly buy day -> caps -> cash check -> split -> validate -> order -> record
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from . import engine, guard
from .bank import Bank, RelinkNeeded
from .broker import Broker, DuplicateOrder, Order
from .config import Config
from .ledger import Ledger

FETCH_DAYS = 14
RECONCILE_DAYS = 30
TEST_ORDER_PREFIX = "spare-test-"


@dataclass
class RunResult:
    status: str  # disabled | not_buy_day | no_targets | pot_too_small | cap_reached | market_closed | cash_short | invalid | dry_run | bought
    new_roundups: int = 0
    pot_cents: int = 0
    plan: dict[str, int] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def reconcile(ledger: Ledger, broker: Broker, day: str) -> list[str]:
    """Record our own orders the ledger missed (e.g. a crash after ordering) and flag any order
    that this tool didn't place. Returns alert messages."""
    alerts = []
    known = ledger.client_order_ids()
    voided = ledger.voided_order_ids()
    alerted = {e.get("client_order_id") for e in ledger.of_type("alert")}
    batch = engine.batch_id(ledger.last_batch_id(), ledger.unbatched_roundup_ids())
    since = (date.fromisoformat(day) - timedelta(days=RECONCILE_DAYS)).isoformat()
    for order in broker.list_orders(since=since):
        coid = order.client_order_id or ""
        if coid.startswith(TEST_ORDER_PREFIX):
            continue  # a manual `spare paper-order` setup test: ours, but not part of the pot
        if not coid.startswith("spare-"):
            if coid not in alerted:  # alert once per order, not on every run
                msg = f"unknown order in your account: {order.side} {order.symbol} (possible leaked key)"
                alerts.append(msg)
                ledger.append("alert", message=msg, client_order_id=coid)
        elif coid in known and order.dead and coid not in voided:
            # We recorded this buy, but it never filled: give the money back to the pot.
            ledger.append("reconciled", client_order_id=coid, void=True, status=order.status)
            alerts.append(f"order for {order.symbol} was {order.status}; its money is back in the pot")
        elif coid not in known and not order.dead:
            ledger.append("reconciled", client_order_id=coid)
            _record_buy(ledger, order, batch, order.submitted_date or day)
    return alerts


def _record_buy(ledger: Ledger, order: Order, batch: str, day: str) -> None:
    ledger.append("buy", date=day, batch_id=batch, symbol=order.symbol,
                  notional_cents=order.notional_cents, client_order_id=order.client_order_id,
                  status=order.status, qty=order.filled_qty, price=order.filled_avg_price)


def collect_roundups(ledger: Ledger, bank: Bank, cfg: Config, day: str) -> int:
    since = (date.fromisoformat(day) - timedelta(days=FETCH_DAYS)).isoformat()
    seen = ledger.seen_txn_ids()
    added = 0
    for txn in sorted(bank.transactions(since, day), key=lambda t: (t.date, t.id)):
        if txn.id in seen or not txn.posted or txn.amount_cents <= 0:
            continue  # pending waits until posted; credits never change the pot
        cents = engine.roundup_for(txn, cfg.multiplier)
        ledger.append("roundup", txn_id=txn.id, date=txn.date, cents=cents,
                      amount_cents=txn.amount_cents, merchant=engine.clean_merchant(txn.description))
        seen.add(txn.id)
        added += 1
    return added


def run(cfg: Config, ledger: Ledger, bank: Bank, broker: Broker, targets: dict[str, float],
        universe: guard.Universe, day: str) -> RunResult:
    if not cfg.enabled:
        return RunResult("disabled", pot_cents=ledger.pot_cents())

    alerts = reconcile(ledger, broker, day)
    try:
        added = collect_roundups(ledger, bank, cfg, day)
    except RelinkNeeded as e:
        # Keep going with the pot we already have; the user just has to re-link.
        added = 0
        ledger.append("alert", message=str(e), kind="relink")
        alerts.append(str(e))
    pot = ledger.pot_cents()
    result = RunResult("not_buy_day", new_roundups=added, pot_cents=pot, errors=alerts)

    last_buy = ledger.last_buy_date()
    if not engine.is_buy_day(date.fromisoformat(day), cfg.buy_weekday,
                             date.fromisoformat(last_buy) if last_buy else None):
        return result
    if not targets:
        result.status = "no_targets"  # nothing approved yet; the pot keeps growing
        return result
    invested = ledger.invested_in_month_cents(day[:7])
    amount = engine.buy_amount(pot, cfg.monthly_cap_cents, invested)
    if amount == 0:
        result.status = "cap_reached" if pot >= engine.MIN_ORDER_CENTS else "pot_too_small"
        return result
    if not broker.market_open(day):
        result.status = "market_closed"
        return result

    # Re-validate the approved targets on every run: a bad file means no buy, never a best effort.
    errors = guard.validate_targets(targets, universe, cfg.limits)
    for symbol in targets:
        errors += guard.check_tradable(symbol, broker.asset(symbol))
    cash = broker.cash_cents()
    if cash < amount:
        msg = f"top up Alpaca: ${amount / 100:.2f} to invest but only ${cash / 100:.2f} cash"
        ledger.append("alert", message=msg)
        result.status, result.errors = "cash_short", alerts + [msg]
        return result

    plan = engine.split(amount, targets, ledger.holdings_cents())
    if not errors:
        errors += guard.validate_buy(amount_cents=amount, invested_this_month_cents=invested,
                                     monthly_cap_cents=cfg.monthly_cap_cents, cash_cents=cash)
        errors += guard.validate_order_plan(plan, targets, amount)
    if errors:
        ledger.append("alert", message="buy blocked by guardrails", errors=errors)
        result.status, result.errors = "invalid", alerts + errors
        return result

    result.plan = plan
    if cfg.dry_run:
        result.status = "dry_run"
        return result

    batch = engine.batch_id(ledger.last_batch_id(), ledger.unbatched_roundup_ids())
    for symbol, cents in plan.items():
        coid = engine.client_order_id(batch, symbol)
        try:
            order = broker.submit_notional_buy(symbol, cents, coid, day)
        except DuplicateOrder:
            continue  # already placed by an earlier attempt; reconcile records it
        _record_buy(ledger, order, batch, day)
        result.orders.append(order)
    result.status = "bought"
    result.pot_cents = ledger.pot_cents()
    return result


def qty_from(notional_cents: int, price_cents: int) -> str:
    """Fractional share quantity as a decimal string (9 places, Alpaca's limit), without floats."""
    return str((Decimal(notional_cents) / Decimal(price_cents)).quantize(Decimal("0.000000001")))
