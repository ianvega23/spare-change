"""The broker interface. Alpaca (phase 2) and Public (phase 10) adapters implement it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class DuplicateOrder(Exception):
    """The broker already has an order with this client_order_id."""


@dataclass(frozen=True)
class Order:
    client_order_id: str
    symbol: str
    notional_cents: int
    status: str  # "filled", "accepted", ...
    filled_qty: str = "0"  # decimal string, never a float
    filled_avg_price_cents: int | None = None
    submitted_date: str = ""


class Broker(Protocol):
    name: str

    def cash_cents(self) -> int: ...

    def market_open(self, day: str) -> bool: ...

    def asset(self, symbol: str) -> dict | None:
        """{'tradable': bool, 'fractionable': bool} or None if unknown."""
        ...

    def submit_notional_buy(self, symbol: str, notional_cents: int, client_order_id: str,
                            day: str) -> Order:
        """A notional market day order. Raises DuplicateOrder if the id was already used."""
        ...

    def list_orders(self) -> list[Order]: ...
