"""Teller adapter (read-only bank data for Chase and others), standard library only.

Follows https://teller.io/docs/api (API version 2020-10-12):
- Every request uses mutual TLS with your Teller client certificate (sandbox doesn't need one),
  plus HTTP Basic auth with the access token as the username and an empty password.
- GET /accounts and GET /accounts/{id}/transactions?start_date=&end_date=&count=&from_id=
- Errors are {"error": {"code", "message"}}. A 404 whose code starts with
  `enrollment.disconnected` (or a 403 for a revoked token) means the user has to re-link.

Teller's docs call `amount` "the signed amount" without fixing the sign for checking accounts, so
the sign is a setting (`spending_sign`) and `spare bank-check` shows how recent transactions are
read, so you can confirm it once against your own account.
"""
from __future__ import annotations

import base64
import json
import random
import ssl
import time
import urllib.parse
from decimal import Decimal, InvalidOperation
from typing import Callable

from .bank import NOT_PURCHASES, BankError, RelinkNeeded
from .engine import Transaction
from .http import NetworkError, Response, Transport, header, make_transport

BASE_URL = "https://api.teller.io"
API_VERSION = "2020-10-12"
PAGE_SIZE = 250
MAX_ATTEMPTS = 4
RETRYABLE = frozenset({429, 500, 502, 503, 504})


def mtls_context(cert_path: str, key_path: str) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return ctx


def to_spent_cents(amount: str, spending_sign: str) -> int:
    """Teller's signed amount string -> cents spent (positive) or received (zero or negative)."""
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        raise BankError(f"unexpected amount from Teller: {amount!r}") from None
    cents = value * 100
    if cents != cents.to_integral_value():
        raise BankError(f"amount has fractions of a cent: {amount!r}")
    cents = int(cents)
    return -cents if spending_sign == "negative" else cents


class TellerBank:
    def __init__(self, access_token: str, *, cert_path: str | None = None, key_path: str | None = None,
                 account_id: str | None = None, spending_sign: str = "negative",
                 environment: str = "production", transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not access_token:
            raise ValueError("a Teller access token is required (run `spare enroll`)")
        if spending_sign not in ("negative", "positive"):
            raise ValueError('spending_sign must be "negative" or "positive"')
        if environment not in ("sandbox", "development", "production"):
            raise ValueError("environment must be sandbox, development or production")
        if transport is None:
            if environment != "sandbox" and not (cert_path and key_path):
                raise ValueError("Teller needs your client certificate and key outside the sandbox")
            transport = make_transport(mtls_context(cert_path, key_path) if cert_path else None)
        self.name = "teller"
        self.account_id = account_id
        self.spending_sign = spending_sign
        self._auth = "Basic " + base64.b64encode(f"{access_token}:".encode()).decode()
        self._transport = transport
        self._sleep = sleep

    def __repr__(self) -> str:  # never show the token
        return f"TellerBank(account_id={self.account_id!r})"

    def _get(self, path: str, params: dict | None = None):
        url = BASE_URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"Authorization": self._auth, "Accept": "application/json",
                   "Teller-Version": API_VERSION, "User-Agent": "spare-change"}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self._transport("GET", url, headers, None)
            except NetworkError as e:
                if attempt == MAX_ATTEMPTS:
                    raise BankError(f"can't reach Teller ({e})") from None
                self._backoff(attempt, None)
                continue
            if resp.status == 200:
                return json.loads(resp.body)
            if resp.status in RETRYABLE and attempt < MAX_ATTEMPTS:
                self._backoff(attempt, resp)
                continue
            raise _error(resp, path)
        raise BankError(f"GET {path} failed")  # pragma: no cover

    def _backoff(self, attempt: int, resp: Response | None) -> None:
        delay = min(8.0, 2.0 ** (attempt - 1)) + random.uniform(0, 0.25)
        retry_after = header(resp, "retry-after") if resp is not None else None
        if retry_after and retry_after.isdigit():
            delay = max(delay, min(30.0, float(retry_after)))
        self._sleep(delay)

    def accounts(self) -> list[dict]:
        return self._get("/accounts")

    def checking_account_id(self) -> str:
        """The configured account, or the one open checking account if there's exactly one."""
        if self.account_id:
            return self.account_id
        checking = [a for a in self.accounts() if a.get("type") == "depository"
                    and a.get("subtype") == "checking" and a.get("status") == "open"]
        if len(checking) != 1:
            raise BankError(f"found {len(checking)} open checking accounts; set teller_account_id "
                            "to the one to round up (see `spare bank-check`)")
        self.account_id = checking[0]["id"]
        return self.account_id

    def raw_transactions(self, since: str, until: str) -> list[dict]:
        account = urllib.parse.quote(self.checking_account_id(), safe="")
        params = {"start_date": since, "end_date": until, "count": str(PAGE_SIZE)}
        out: list[dict] = []
        while True:
            page = self._get(f"/accounts/{account}/transactions", params)
            out += page
            if len(page) < PAGE_SIZE:
                return out
            params["from_id"] = page[-1]["id"]  # older than the last one on this page

    def transactions(self, since: str, until: str) -> list[Transaction]:
        out = []
        for t in self.raw_transactions(since, until):
            spent = to_spent_cents(t.get("amount", "0"), self.spending_sign)
            if (t.get("type") or "").lower() in NOT_PURCHASES:
                spent = -abs(spent)  # never round up a transfer or deposit
            out.append(Transaction(id=t["id"], date=t["date"],
                                   description=(t.get("description") or "")[:80],
                                   amount_cents=spent, posted=t.get("status") == "posted"))
        return out


def _error(resp: Response, path: str) -> BankError:
    try:
        err = json.loads(resp.body or b"{}").get("error") or {}
    except (ValueError, AttributeError):
        err = {}
    code, message = str(err.get("code") or ""), str(err.get("message") or "")[:200]
    detail = f"HTTP {resp.status}" + (f" {code}" if code else "") + (f": {message}" if message else "")
    if code.startswith("enrollment.disconnected") or resp.status == 403:
        return RelinkNeeded(f"your bank connection needs attention ({detail}); run `spare enroll` again")
    if resp.status == 400 and "certificate" in message.lower():
        return BankError(f"Teller wants your client certificate ({detail})")
    return BankError(f"GET {path} failed ({detail})")
