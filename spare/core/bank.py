"""The bank interface. Teller (phase 3) and Plaid adapters implement it."""
from __future__ import annotations

from typing import Protocol

from .engine import Transaction


class Bank(Protocol):
    name: str

    def transactions(self, since: str, until: str) -> list[Transaction]:
        """Transactions dated from `since` to `until` (ISO dates, inclusive). Amounts are positive
        for money spent. Includes pending ones, flagged `posted=False`."""
        ...
