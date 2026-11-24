"""Fake bank and broker for tests and the demo. No network, deterministic from a seed."""
from __future__ import annotations

import random
from datetime import date, timedelta

from .core.broker import DuplicateOrder, Order
from .core.engine import Transaction
from .core.invest import qty_from

MERCHANTS = ["Coffee shop", "Burrito place", "Gas station", "Grocery store", "Online store",
             "Rideshare", "Big-box store", "Music streaming", "Pharmacy", "Bakery",
             "Video streaming", "Hardware store"]


class FakeBank:
    """A checking account with a few card purchases a day, plus refunds, a paycheck and
    pending charges that post the next day."""
    name = "fake-bank"

    def __init__(self, start: str, days: int, seed: int = 7, per_day: tuple[int, int] = (1, 4)):
        rng = random.Random(seed)
        first = date.fromisoformat(start)
        self._txns: list[Transaction] = []
        for d in range(days):
            day = (first + timedelta(days=d)).isoformat()
            for i in range(rng.randint(*per_day)):
                cents = rng.randint(150, 9_000)
                if rng.random() < 0.1:
                    cents = rng.randint(1, 50) * 100  # whole-dollar charges add nothing
                self._txns.append(Transaction(f"t-{day}-{i}", day, rng.choice(MERCHANTS), cents))
            if d % 14 == 0:
                self._txns.append(Transaction(f"pay-{day}", day, "Payroll deposit", -185_000))
            if d % 9 == 4:
                self._txns.append(Transaction(f"refund-{day}", day, "Online store refund", -2_399))
        self.pending_until: dict[str, str] = {}
        for t in self._txns[::11]:  # some charges show as pending on their first day
            self.pending_until[t.id] = (date.fromisoformat(t.date) + timedelta(days=1)).isoformat()

    def transactions(self, since: str, until: str) -> list[Transaction]:
        out = []
        for t in self._txns:
            if since <= t.date <= until:
                posted = self.pending_until.get(t.id, t.date) <= until
                out.append(Transaction(t.id, t.date, t.description, t.amount_cents, posted))
        return out


class FakeBroker:
    """Fills every notional order instantly at a fixed price per symbol. Weekends are closed.
    Rejects a reused client_order_id like Alpaca does."""
    name = "fake-broker"

    def __init__(self, cash_cents: int = 100_000, prices_cents: dict[str, int] | None = None,
                 untradable: set[str] = frozenset()):
        self._cash = cash_cents
        self.prices = prices_cents or {}
        self.untradable = set(untradable)
        self.orders: dict[str, Order] = {}

    def deposit(self, cents: int) -> None:
        self._cash += cents

    def cash_cents(self) -> int:
        return self._cash

    def market_open(self, day: str) -> bool:
        return date.fromisoformat(day).weekday() < 5

    def asset(self, symbol: str) -> dict | None:
        ok = symbol not in self.untradable
        return {"tradable": ok, "fractionable": ok}

    def submit_notional_buy(self, symbol: str, notional_cents: int, client_order_id: str,
                            day: str) -> Order:
        if client_order_id in self.orders:
            raise DuplicateOrder(client_order_id)
        price = self.prices.get(symbol, 10_000)
        order = Order(client_order_id, symbol, notional_cents, "filled",
                      qty_from(notional_cents, price), price, day)
        self.orders[client_order_id] = order
        self._cash -= notional_cents
        return order

    def list_orders(self) -> list[Order]:
        return list(self.orders.values())
