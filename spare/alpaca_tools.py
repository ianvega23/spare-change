"""Manual Alpaca checks for setup (phase 2). Paper only. Keys come from environment variables."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from .core import config as config_mod
from .core import guard, invest
from .core.broker import BrokerError
from .core.broker_alpaca import AlpacaBroker

KEY_ENV, SECRET_ENV = "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"


def paper_broker_from_env() -> AlpacaBroker:
    key, secret = os.environ.get(KEY_ENV), os.environ.get(SECRET_ENV)
    if not key or not secret:
        raise SystemExit(f"Set {KEY_ENV} and {SECRET_ENV} to your Alpaca *paper* keys first.")
    return AlpacaBroker(key, secret, env="paper")


def alpaca_check(broker, out=print) -> int:
    """Read-only: account cash, market clock, and whether each allowlisted ETF can be bought."""
    out(f"Broker: {broker.name}")
    out(f"Cash: ${broker.cash_cents() / 100:,.2f}")
    out(f"Market open now: {'yes' if broker.market_open('') else 'no'}")
    problems = 0
    for symbol in sorted(config_mod.load_universe().etfs):
        errors = guard.check_tradable(symbol, broker.asset(symbol))
        problems += bool(errors)
        out(f"  {symbol:<5} {'ok' if not errors else '; '.join(errors)}")
    out("All allowlisted ETFs are tradable and fractionable." if not problems
        else f"{problems} allowlisted ETF(s) can't be bought fractionally on this account.")
    return 0 if not problems else 1


def paper_order(broker, symbol: str, cents: int, out=print) -> int:
    """Place one small paper buy, then find it again in the order list to prove the round trip.
    Test orders use the spare-test- prefix, so the invest job neither counts nor flags them."""
    if broker.env != "paper":
        raise SystemExit("paper-order only runs against Alpaca paper trading.")
    if not 100 <= cents <= 2_500:
        raise SystemExit("paper-order is for a test buy between $1 and $25.")
    errors = guard.check_tradable(symbol, broker.asset(symbol))
    if errors:
        raise SystemExit("; ".join(errors))
    if not broker.market_open(""):
        out("The market is closed, so Alpaca will queue the order for the next open.")
    now = datetime.now(timezone.utc)
    coid = invest.TEST_ORDER_PREFIX + hashlib.sha256(f"{now.isoformat()}{symbol}".encode()).hexdigest()[:16]
    try:
        order = broker.submit_notional_buy(symbol, cents, coid, now.date().isoformat())
    except BrokerError as e:
        out(f"Order failed: {e}")
        return 1
    out(f"Placed: buy ${cents / 100:.2f} of {symbol}, status {order.status}, id {coid}")

    listed = [o for o in broker.list_orders(since=now.date().isoformat()) if o.client_order_id == coid]
    looked_up = broker.order_by_client_id(coid)
    ok = bool(listed) and looked_up is not None and not looked_up.dead
    out(f"Found again in the order list and by id: {'yes' if ok else 'no'}"
        + (f" (status {looked_up.status}, filled {looked_up.filled_qty} at {looked_up.filled_avg_price})"
           if looked_up else ""))
    return 0 if ok else 1
