"""The broker interface. Alpaca (phase 2) and Public (phase 10) adapters implement it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class BrokerError(Exception):
    """The broker refused a request or couldn't be reached. Never retried blindly for orders."""


class DuplicateOrder(BrokerError):
    """The broker already has an order with this client_order_id."""


# Order statuses that ended without buying anything. Anything else counts as money committed.
DEAD_STATUSES = frozenset({"canceled", "expired", "rejected", "suspended"})


@dataclass(frozen=True)
class Order:
    client_order_id: str
    symbol: str
    notional_cents: int
    status: str  # "filled", "accepted", ...
    filled_qty: str = "0"  # decimal string, kept exactly as the broker sent it
    filled_avg_price: str | None = None  # decimal string, kept exactly as the broker sent it
    submitted_date: str = ""
    side: str = "buy"

    @property
    def dead(self) -> bool:
        """Ended without a fill (canceled, expired, rejected)."""
        return self.status in DEAD_STATUSES and self.filled_qty in ("0", "", None)


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

    def list_orders(self, since: str | None = None) -> list[Order]:
        """Recent orders, all statuses. `since` is an ISO date."""
        ...
