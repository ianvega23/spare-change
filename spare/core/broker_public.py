"""Public.com Trading API adapter, standard library only. Tested with mocks only.

Public has no paper or sandbox account: every order is real money. So the adapter can always read
(balances, positions, orders, which is what a dry run needs), but it refuses to place an order
unless it was built with `allow_orders=True`, which the invest job passes only when dry_run is off
and the go-live checklist has passed on Alpaca paper. Endpoints and fields follow Public's API docs (public.com/api/docs), read on
2026-11-25; see docs/adr/0003-public-adapter.md for what the docs left open.

Safety rules in this file:
- Public's minimum fractional order is $5 (`min_order_cents`), so the split never makes smaller slices.
- Reads are retried on rate limits (10 requests/second), server errors and network errors. An order
  is sent once; after an unclear failure it is looked up by its id, and if it isn't found it is
  resent with the same id, which Public uses to drop duplicates.
- Cash means cash-only buying power: v1 never uses margin, and every order says `useMargin: false`.
- The secret key and access token are never logged or put in errors.
"""
from __future__ import annotations

import json
import random
import time
import urllib.parse
from decimal import Decimal, InvalidOperation
from typing import Callable

from .broker import BrokerError, Order
from .broker_alpaca import cents_to_notional, dollars_to_cents
from .http import NetworkError, Response, Transport, header, make_transport

BASE_URL = "https://api.public.com"
TOKEN_MINUTES = 60
MIN_ORDER_CENTS = 500
MAX_ATTEMPTS = 4
RETRYABLE = frozenset({429, 500, 502, 503, 504})
# Public order ids must be RFC 4122 UUIDs. Ours carry the 24 hex digits of our client_order_id
# behind this fixed prefix, so an order can be mapped back to the batch that placed it.
UUID_PREFIX = "5ba7c0"
STATUSES = {"NEW": "new", "PARTIALLY_FILLED": "partially_filled", "FILLED": "filled",
            "CANCELLED": "canceled", "QUEUED_CANCELLED": "canceled", "REJECTED": "rejected",
            "EXPIRED": "expired", "PENDING_REPLACE": "pending_replace",
            "PENDING_CANCEL": "pending_cancel", "REPLACED": "replaced"}


def order_uuid(client_order_id: str) -> str:
    """'spare-' + 24 hex -> a version-4-shaped UUID that keeps all 24 digits."""
    digits = client_order_id.removeprefix("spare-")
    if not client_order_id.startswith("spare-") or len(digits) != 24 or \
            any(c not in "0123456789abcdef" for c in digits):
        raise ValueError("client_order_id must be 'spare-' and 24 lowercase hex digits")
    h = UUID_PREFIX + digits  # 30 hex digits; add the version (4) and variant (8) nibbles
    h = h[:12] + "4" + h[12:15] + "8" + h[15:]
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def client_id_from_uuid(order_id: str) -> str:
    """The reverse of order_uuid; any other id (an order we didn't place) comes back unchanged."""
    h = (order_id or "").replace("-", "").lower()
    if len(h) != 32 or not h.startswith(UUID_PREFIX) or h[12] != "4" or h[16] != "8":
        return order_id or ""
    digits = h[:12] + h[13:16] + h[17:]
    return "spare-" + digits[len(UUID_PREFIX):]


def _message(resp: Response) -> str:
    try:
        data = json.loads(resp.body or b"{}")
        text = data.get("message") or data.get("error") or ""
    except (ValueError, AttributeError):
        text = ""
    return f"HTTP {resp.status}" + (f": {str(text)[:200]}" if text else "")


class PublicBroker:
    min_order_cents = MIN_ORDER_CENTS
    label = "Public"

    def __init__(self, secret_key: str, *, allow_orders: bool = False, account_id: str | None = None,
                 transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time):
        if not secret_key:
            raise ValueError("PUBLIC_SECRET_KEY is required")
        self.env = "live"
        self.name = "public-live"
        self.allow_orders = allow_orders
        self._secret = secret_key
        self._transport = transport or make_transport()
        self._sleep, self._clock = sleep, clock
        self._token: str | None = None
        self._token_expires = 0.0
        self._account = account_id

    def __repr__(self) -> str:
        return "PublicBroker()"

    # ---- HTTP ------------------------------------------------------------------------------

    def _access_token(self) -> str:
        if self._token and self._clock() < self._token_expires - 120:
            return self._token
        body = json.dumps({"validityInMinutes": TOKEN_MINUTES, "secret": self._secret}).encode()
        try:
            resp = self._transport("POST", BASE_URL + "/userapiauthservice/personal/access-tokens",
                                   {"Content-Type": "application/json", "Accept": "application/json",
                                    "User-Agent": "spare-change"}, body)
        except NetworkError as e:
            raise BrokerError(f"can't reach Public ({e})") from None
        if resp.status != 200:
            raise BrokerError(f"Public refused the secret key ({_message(resp)})")
        token = (json.loads(resp.body or b"{}") or {}).get("accessToken")
        if not token:
            raise BrokerError("Public sent no access token")
        self._token, self._token_expires = token, self._clock() + TOKEN_MINUTES * 60
        return token

    def _call(self, method: str, path: str, payload: dict | None = None, *, allow_404: bool = False,
              retry: bool = True):
        url = BASE_URL + path
        body = json.dumps(payload).encode() if payload is not None else None
        attempts = MAX_ATTEMPTS if retry else 1
        for attempt in range(1, attempts + 1):
            headers = {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json",
                       "User-Agent": "spare-change"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            try:
                resp = self._transport(method, url, headers, body)
            except NetworkError as e:
                if attempt == attempts:
                    raise BrokerError(f"can't reach Public ({e})") from None
                self._backoff(attempt, None)
                continue
            if resp.status == 200:
                return json.loads(resp.body or b"null")
            if resp.status == 404 and allow_404:
                return None
            if resp.status == 401 and attempt < attempts:
                self._token = None  # expired early: get a fresh one
                continue
            if resp.status in RETRYABLE and attempt < attempts:
                self._backoff(attempt, resp)
                continue
            raise BrokerError(f"{method} {path.split('/')[-1] or path} failed ({_message(resp)})")
        raise BrokerError(f"{method} {path} failed")  # pragma: no cover

    def _backoff(self, attempt: int, resp: Response | None) -> None:
        delay = min(8.0, 2.0 ** (attempt - 1)) + random.uniform(0, 0.25)
        retry_after = header(resp, "retry-after") if resp is not None else None
        if retry_after and retry_after.isdigit():
            delay = max(delay, min(30.0, float(retry_after)))
        self._sleep(delay)

    def account_id(self) -> str:
        if self._account:
            return self._account
        accounts = (self._call("GET", "/userapigateway/trading/account") or {}).get("accounts") or []
        brokerage = [a for a in accounts if a.get("accountType") == "BROKERAGE"]
        if len(brokerage) != 1:
            raise BrokerError(f"expected one Public brokerage account, found {len(brokerage)}; "
                              "set public_account_id in config.toml")
        self._account = str(brokerage[0].get("accountId") or "")
        if not self._account:
            raise BrokerError("Public returned an account without an id")
        return self._account

    def _portfolio(self) -> dict:
        path = f"/userapigateway/trading/{urllib.parse.quote(self.account_id(), safe='')}/portfolio/v2"
        return self._call("GET", path) or {}

    # ---- Broker interface ------------------------------------------------------------------------

    def cash_cents(self) -> int:
        power = self._portfolio().get("buyingPower") or {}
        return dollars_to_cents(power.get("cashOnlyBuyingPower"))

    def market_open(self, day: str) -> bool:
        """Public documents no market clock, so: weekdays. On a holiday a DAY order is rejected or
        expires unfilled, and reconcile returns its money to the pot."""
        from datetime import date
        return date.fromisoformat(day).weekday() < 5

    def asset(self, symbol: str) -> dict | None:
        path = f"/userapigateway/trading/instruments/{urllib.parse.quote(symbol, safe='')}/EQUITY"
        data = self._call("GET", path, allow_404=True)
        if not data:
            return None
        return {"tradable": data.get("trading") == "BUY_AND_SELL",
                "fractionable": data.get("fractionalTrading") == "BUY_AND_SELL"}

    def _order(self, order_id: str) -> Order | None:
        path = (f"/userapigateway/trading/{urllib.parse.quote(self.account_id(), safe='')}"
                f"/order/{urllib.parse.quote(order_id, safe='')}")
        data = self._call("GET", path, allow_404=True)
        return _parse_order(data) if data else None

    def order_by_client_id(self, client_order_id: str) -> Order | None:
        return self._order(order_uuid(client_order_id))

    def submit_notional_buy(self, symbol: str, notional_cents: int, client_order_id: str,
                            day: str) -> Order:
        if not self.allow_orders:
            raise BrokerError("Public has no paper trading, so orders stay locked until dry_run is off "
                              "and the go-live checklist has passed")
        if notional_cents < MIN_ORDER_CENTS:
            raise BrokerError(f"order for {symbol} is under Public's $5 minimum")
        oid = order_uuid(client_order_id)
        payload = {"orderId": oid, "instrument": {"symbol": symbol, "type": "EQUITY"},
                   "orderSide": "BUY", "orderType": "MARKET", "expiration": {"timeInForce": "DAY"},
                   "amount": cents_to_notional(notional_cents), "useMargin": False}
        path = f"/userapigateway/trading/{urllib.parse.quote(self.account_id(), safe='')}/order"
        try:
            self._call("POST", path, payload, retry=False)
        except BrokerError as first:
            # Unclear whether Public got it. Look it up; if it isn't there, resend with the same
            # orderId, which Public documents as safe (it drops duplicates).
            existing = self.order_by_client_id(client_order_id)
            if existing is not None:
                return existing
            if "HTTP 4" in str(first) and "HTTP 429" not in str(first):
                raise BrokerError(f"order for {symbol} was refused ({str(first).split('(', 1)[-1]}") from None
            self._call("POST", path, payload, retry=False)
        placed = self.order_by_client_id(client_order_id)
        # Placement is asynchronous: if it isn't visible yet, record it as new; reconcile catches up.
        return placed or Order(client_order_id, symbol, notional_cents, "new", "0", None, day, "buy")

    def list_orders(self, since: str | None = None) -> list[Order]:
        """Up to 500 orders from the last 30 days (Public's documented limit for order search)."""
        body = {"createdAfter": since + "T00:00:00Z"} if since else {}
        path = f"/userapigateway/trading/{urllib.parse.quote(self.account_id(), safe='')}/order/search"
        data = self._call("POST", path, body) or {}
        return [_parse_order(o) for o in data.get("orders") or []]

    def positions_cents(self) -> dict[str, int]:
        out = {}
        for p in self._portfolio().get("positions") or []:
            symbol = (p.get("instrument") or {}).get("symbol")
            if symbol:
                out[symbol] = dollars_to_cents(p.get("currentValue"))
        return out

    def latest_price(self, symbol: str) -> str | None:
        """From the portfolio (Public's market data endpoints aren't used), so only for holdings."""
        for p in self._portfolio().get("positions") or []:
            if (p.get("instrument") or {}).get("symbol") == symbol:
                price = (p.get("lastPrice") or {}).get("lastPrice")
                try:
                    return str(Decimal(str(price))) if price not in (None, "") else None
                except InvalidOperation:
                    return None
        return None


def _parse_order(data: dict) -> Order:
    notional = data.get("notionalValue")
    if notional in (None, ""):
        try:
            notional = str(Decimal(data.get("filledQuantity") or 0) * Decimal(data.get("averagePrice") or 0))
        except InvalidOperation:
            notional = "0"
    return Order(
        client_order_id=client_id_from_uuid(data.get("orderId") or ""),
        symbol=(data.get("instrument") or {}).get("symbol") or "",
        notional_cents=dollars_to_cents(notional),
        status=STATUSES.get(str(data.get("status") or "").upper(), str(data.get("status") or "unknown").lower()),
        filled_qty=str(data.get("filledQuantity") or "0"),
        filled_avg_price=data.get("averagePrice"),
        submitted_date=str(data.get("createdAt") or "")[:10],
        side=str(data.get("side") or "").lower(),
    )
