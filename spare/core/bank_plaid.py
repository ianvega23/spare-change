"""Plaid adapter, the fallback bank source. Standard library only, same interface as Teller.

Uses POST /transactions/get with start_date/end_date and offset paging. Plaid's convention is
positive amount = money out, which matches ours. Amounts are JSON numbers, so they're parsed
straight to Decimal, never float. ITEM_LOGIN_REQUIRED means the user must re-link.
"""
from __future__ import annotations

import json
import random
import time
from decimal import Decimal
from typing import Callable

from .bank import NOT_PURCHASES, BankError, RelinkNeeded
from .engine import Transaction
from .http import NetworkError, Transport, make_transport

BASE_URLS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
PAGE_SIZE = 500
MAX_ATTEMPTS = 4
RELINK_CODES = frozenset({"ITEM_LOGIN_REQUIRED", "PENDING_EXPIRATION", "ACCESS_NOT_GRANTED",
                          "INVALID_ACCESS_TOKEN", "ITEM_NOT_FOUND"})


class PlaidBank:
    def __init__(self, client_id: str, secret: str, access_token: str, *, environment: str = "production",
                 account_id: str | None = None, transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if environment not in BASE_URLS:
            raise ValueError("environment must be sandbox or production")
        if not (client_id and secret and access_token):
            raise ValueError("Plaid needs a client id, secret and access token")
        self.name = "plaid"
        self.account_id = account_id
        self._base = BASE_URLS[environment]
        self._creds = {"client_id": client_id, "secret": secret, "access_token": access_token}
        self._transport = transport or make_transport()
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"PlaidBank(account_id={self.account_id!r})"

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps({**self._creds, **body}).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "spare-change"}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self._transport("POST", self._base + path, headers, data)
            except NetworkError as e:
                if attempt == MAX_ATTEMPTS:
                    raise BankError(f"can't reach Plaid ({e})") from None
                self._sleep(min(8.0, 2.0 ** (attempt - 1)) + random.uniform(0, 0.25))
                continue
            payload = json.loads(resp.body or b"{}", parse_float=Decimal)
            if resp.status == 200:
                return payload
            code = str(payload.get("error_code") or "")
            if code == "PRODUCT_NOT_READY" or resp.status in (429, 500, 502, 503):
                if attempt < MAX_ATTEMPTS:  # reads only: safe to retry
                    self._sleep(min(8.0, 2.0 ** (attempt - 1)) + random.uniform(0, 0.25))
                    continue
            message = str(payload.get("error_message") or "")[:200]
            if code in RELINK_CODES:
                raise RelinkNeeded(f"your bank connection needs attention (Plaid {code}); re-link it")
            raise BankError(f"Plaid {path} failed (HTTP {resp.status} {code}: {message})")
        raise BankError(f"Plaid {path} failed")  # pragma: no cover

    def transactions(self, since: str, until: str) -> list[Transaction]:
        out, offset = [], 0
        while True:
            options = {"count": PAGE_SIZE, "offset": offset}
            if self.account_id:
                options["account_ids"] = [self.account_id]
            page = self._post("/transactions/get", {"start_date": since, "end_date": until,
                                                    "options": options})
            for t in page.get("transactions", []):
                cents = Decimal(str(t["amount"])) * 100
                if cents != cents.to_integral_value():
                    raise BankError(f"amount has fractions of a cent: {t['amount']!r}")
                spent = int(cents)
                category = str((t.get("personal_finance_category") or {}).get("primary") or "").lower()
                if category in ("transfer_in", "transfer_out", "income") or \
                        (t.get("transaction_type") or "").lower() in NOT_PURCHASES:
                    spent = -abs(spent)  # transfers and income never round up
                out.append(Transaction(id=t["transaction_id"], date=t["date"],
                                       description=(t.get("name") or "")[:80], amount_cents=spent,
                                       posted=not t.get("pending", False)))
            offset += len(page.get("transactions", []))
            if not page.get("transactions") or offset >= int(page.get("total_transactions", 0)):
                return out
