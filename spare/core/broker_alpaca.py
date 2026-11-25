"""Alpaca Trading API adapter, standard library only.

Endpoints and order fields follow Alpaca's official skills repo (github.com/alpacahq/alpaca-skills),
reviewed at the commit pinned in docs/adr/0001-alpaca-reference.md.

Safety rules in this file:
- Paper is the default. Live needs `env="live"` *and* `allow_live=True`; the invest job only passes
  that once the go-live checklist has passed (phase 9).
- Reads (GET) are retried on rate limits, server errors and network errors. Order submission is
  never retried: after an unclear failure we look the order up by client_order_id instead.
- Money stays as decimal strings or integer cents. Keys are never logged or put in errors.
"""
from __future__ import annotations

import json
import random
import time
import urllib.parse
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Callable

from .broker import BrokerError, DuplicateOrder, Order
from .http import NetworkError, Response, Transport, header, make_transport

BASE_URLS = {"paper": "https://paper-api.alpaca.markets", "live": "https://api.alpaca.markets"}
DATA_URL = "https://data.alpaca.markets"  # market data, same host for paper and live
MAX_GET_ATTEMPTS = 4
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


urllib_transport = make_transport()
_NetworkError = NetworkError  # kept for readability in tests


def dollars_to_cents(value: str | None) -> int:
    """'1234.567' -> 123456 (rounded down, so we never think we have more than we do)."""
    if value in (None, ""):
        return 0
    try:
        return int((Decimal(str(value)) * 100).to_integral_value(rounding=ROUND_DOWN))
    except InvalidOperation:
        raise BrokerError(f"unexpected amount from Alpaca: {value!r}") from None


def cents_to_notional(cents: int) -> str:
    """Integer cents -> Alpaca's 2-decimal notional string."""
    if not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
        raise ValueError("notional must be a positive whole number of cents")
    return f"{cents // 100}.{cents % 100:02d}"


def _error_message(resp: Response) -> str:
    try:
        data = json.loads(resp.body or b"{}")
        message = data.get("message") or data.get("error") or ""
    except (ValueError, AttributeError):
        message = ""
    return f"HTTP {resp.status}" + (f": {str(message)[:200]}" if message else "")


class AlpacaBroker:
    def __init__(self, key_id: str, secret_key: str, env: str = "paper", *,
                 allow_live: bool = False, transport: Transport = urllib_transport,
                 sleep: Callable[[float], None] = time.sleep):
        if env not in BASE_URLS:
            raise ValueError('env must be "paper" or "live"')
        if env == "live" and not allow_live:
            raise ValueError("live trading is locked until the paper go-live checklist passes")
        if not key_id or not secret_key:
            raise ValueError("Alpaca key id and secret are required")
        self.env = env
        self.name = f"alpaca-{env}"
        self.label = "Alpaca"
        self._base = BASE_URLS[env]
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key,
                         "Accept": "application/json", "User-Agent": "spare-change"}
        self._transport = transport
        self._sleep = sleep

    def __repr__(self) -> str:  # never show keys
        return f"AlpacaBroker(env={self.env!r})"

    # ---- HTTP ---------------------------------------------------------------------------

    def _url(self, path: str, params: dict | None = None, base: str | None = None) -> str:
        query = "?" + urllib.parse.urlencode(params) if params else ""
        return (base or self._base) + path + query

    def _get(self, path: str, params: dict | None = None, *, allow_404: bool = False,
             base: str | None = None):
        url = self._url(path, params, base)
        for attempt in range(1, MAX_GET_ATTEMPTS + 1):
            try:
                resp = self._transport("GET", url, dict(self._headers), None)
            except _NetworkError as e:
                if attempt == MAX_GET_ATTEMPTS:
                    raise BrokerError(f"can't reach Alpaca ({e})") from None
                self._backoff(attempt, None)
                continue
            if resp.status == 200:
                return json.loads(resp.body)
            if resp.status == 404 and allow_404:
                return None
            if resp.status in RETRYABLE_STATUSES and attempt < MAX_GET_ATTEMPTS:
                self._backoff(attempt, resp)
                continue
            if resp.status in (401, 403):
                raise BrokerError(f"Alpaca refused the keys ({_error_message(resp)})")
            raise BrokerError(f"GET {path} failed ({_error_message(resp)})")
        raise BrokerError(f"GET {path} failed")  # pragma: no cover

    def _backoff(self, attempt: int, resp: Response | None) -> None:
        delay = min(8.0, 2.0 ** (attempt - 1)) + random.uniform(0, 0.25)
        if resp is not None:
            retry_after = header(resp, "retry-after")
            if retry_after and retry_after.isdigit():
                delay = max(delay, min(30.0, float(retry_after)))
        self._sleep(delay)

    # ---- Broker interface -----------------------------------------------------------------

    def account(self) -> dict:
        return self._get("/v2/account")

    def cash_cents(self) -> int:
        acct = self.account()
        if acct.get("trading_blocked") or acct.get("account_blocked"):
            raise BrokerError("the Alpaca account is blocked from trading")
        if acct.get("status") not in (None, "ACTIVE"):
            raise BrokerError(f"the Alpaca account is {acct.get('status')}, not ACTIVE")
        # Cash, not buying power: v1 never uses margin.
        return dollars_to_cents(acct.get("cash"))

    def market_open(self, day: str) -> bool:
        """Whether the market is open right now. `day` is unused: the clock is authoritative."""
        return bool(self._get("/v2/clock").get("is_open"))

    def asset(self, symbol: str) -> dict | None:
        data = self._get(f"/v2/assets/{urllib.parse.quote(symbol, safe='')}", allow_404=True)
        if data is None:
            return None
        return {"tradable": bool(data.get("tradable")) and data.get("status") == "active",
                "fractionable": bool(data.get("fractionable"))}

    def order_by_client_id(self, client_order_id: str) -> Order | None:
        data = self._get("/v2/orders:by_client_order_id",
                         {"client_order_id": client_order_id}, allow_404=True)
        return _parse_order(data) if data else None

    def submit_notional_buy(self, symbol: str, notional_cents: int, client_order_id: str,
                            day: str) -> Order:
        body = json.dumps({"symbol": symbol, "notional": cents_to_notional(notional_cents),
                           "side": "buy", "type": "market", "time_in_force": "day",
                           "client_order_id": client_order_id}).encode()
        headers = {**self._headers, "Content-Type": "application/json"}
        try:
            resp = self._transport("POST", self._url("/v2/orders"), headers, body)
        except _NetworkError:
            resp = None
        if resp is not None and resp.status in (200, 201):
            return _parse_order(json.loads(resp.body))
        if resp is not None and resp.status == 422 and b"client_order_id" in (resp.body or b""):
            raise DuplicateOrder(client_order_id)
        if resp is None or resp.status >= 500:
            # Unclear whether Alpaca got it. Look it up instead of sending it again.
            existing = self.order_by_client_id(client_order_id)
            if existing is not None:
                return existing
            raise BrokerError(f"order for {symbol} may not have been placed; the next run will check")
        if resp.status == 403:
            raise BrokerError(f"not enough buying power for {symbol} ({_error_message(resp)})")
        raise BrokerError(f"order for {symbol} was refused ({_error_message(resp)})")

    def list_orders(self, since: str | None = None) -> list[Order]:
        params = {"status": "all", "limit": "500", "direction": "asc", "nested": "false"}
        if since:
            params["after"] = since + "T00:00:00Z"
        orders: list[Order] = []
        while True:
            page = self._get("/v2/orders", params)
            orders += [_parse_order(o) for o in page]
            if len(page) < 500:
                return orders
            params["after"] = page[-1]["submitted_at"]  # next page, oldest first

    def positions_cents(self) -> dict[str, int]:
        return {p.get("symbol", ""): dollars_to_cents(p.get("market_value"))
                for p in self._get("/v2/positions") if p.get("symbol")}

    def latest_price(self, symbol: str) -> str | None:
        data = self._get(f"/v2/stocks/{urllib.parse.quote(symbol, safe='')}/trades/latest",
                         base=DATA_URL, allow_404=True)
        price = ((data or {}).get("trade") or {}).get("p")
        if price in (None, ""):
            return None
        try:
            return str(Decimal(str(price)))
        except InvalidOperation:
            raise BrokerError(f"unexpected price from Alpaca: {price!r}") from None


def _parse_order(data: dict) -> Order:
    notional = data.get("notional")
    if notional in (None, ""):
        # Orders placed by share count (not by us) have no notional; estimate from the fill.
        try:
            notional = str(Decimal(data.get("filled_qty") or 0) * Decimal(data.get("filled_avg_price") or 0))
        except InvalidOperation:
            notional = "0"
    return Order(
        client_order_id=data.get("client_order_id") or "",
        symbol=data.get("symbol") or "",
        notional_cents=dollars_to_cents(notional),
        status=data.get("status") or "unknown",
        filled_qty=data.get("filled_qty") or "0",
        filled_avg_price=data.get("filled_avg_price"),
        submitted_date=(data.get("submitted_at") or data.get("created_at") or "")[:10],
        side=data.get("side") or "",
    )
