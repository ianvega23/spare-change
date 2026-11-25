"""The bank interface. Teller (default) and Plaid adapters implement it."""
from __future__ import annotations

from typing import Protocol

from .engine import Transaction

# Transaction types that are never purchases, whatever their sign.
NOT_PURCHASES = frozenset({"transfer", "deposit", "interest", "dividend", "payroll", "refund"})


class BankError(Exception):
    """The bank data source refused a request or couldn't be reached."""


class RelinkNeeded(BankError):
    """The bank connection needs the user: re-run `spare enroll` (MFA, password change, revoked)."""


class Bank(Protocol):
    name: str

    def transactions(self, since: str, until: str) -> list[Transaction]:
        """Transactions dated from `since` to `until` (ISO dates, inclusive). Amounts are positive
        for money spent. Includes pending ones, flagged `posted=False`."""
        ...
