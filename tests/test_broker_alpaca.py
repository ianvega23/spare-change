"""The Alpaca adapter against a scripted fake transport. No network."""
import json
import unittest

from spare import alpaca_tools
from spare.core import invest
from spare.core.broker import BrokerError, DuplicateOrder
from spare.core.broker_alpaca import (AlpacaBroker, Response, _NetworkError, cents_to_notional,
                                      dollars_to_cents)
from spare.core.config import from_dict
from tests.helpers import UNIVERSE, TempDirCase

KEY, SECRET = "PKTESTKEY123", "secret-do-not-leak"


def order_json(coid, symbol="VTI", notional="17.50", status="filled", qty="0.056451613",
               price="310.00", submitted="2026-03-02T15:00:01Z", side="buy"):
    return {"id": "uuid-" + coid, "client_order_id": coid, "symbol": symbol, "notional": notional,
            "status": status, "filled_qty": qty, "filled_avg_price": price,
            "submitted_at": submitted, "side": side}


class FakeTransport:
    """Routes (method, path) to scripted responses; records every call."""

    def __init__(self):
        self.calls = []
        self.routes = {}

    def on(self, method, path, *responses):
        self.routes[(method, path)] = list(responses)

    def __call__(self, method, url, headers, body):
        path = url.split("alpaca.markets", 1)[1].split("?", 1)[0]
        self.calls.append((method, url, headers, body))
        queue = self.routes.get((method, path))
        if not queue:
            return Response(404, b'{"message":"not found"}', {})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, payload = item[0], item[1]
        headers = item[2] if len(item) > 2 else {}
        return Response(status, json.dumps(payload).encode(), headers)


class AlpacaTests(unittest.TestCase):
    def setUp(self):
        self.t = FakeTransport()
        self.sleeps = []
        self.b = AlpacaBroker(KEY, SECRET, transport=self.t, sleep=self.sleeps.append)

    # -- construction and safety ---------------------------------------------------------

    def test_paper_by_default(self):
        self.assertEqual(self.b.env, "paper")
        self.t.on("GET", "/v2/clock", (200, {"is_open": True}))
        self.b.market_open("")
        self.assertTrue(self.t.calls[0][1].startswith("https://paper-api.alpaca.markets/"))

    def test_live_is_locked(self):
        with self.assertRaises(ValueError):
            AlpacaBroker(KEY, SECRET, env="live")
        live = AlpacaBroker(KEY, SECRET, env="live", allow_live=True, transport=self.t)
        self.t.on("GET", "/v2/clock", (200, {"is_open": False}))
        live.market_open("")
        self.assertTrue(self.t.calls[0][1].startswith("https://api.alpaca.markets/"))

    def test_needs_keys(self):
        with self.assertRaises(ValueError):
            AlpacaBroker("", SECRET)

    def test_keys_never_in_repr_or_errors(self):
        self.assertNotIn(SECRET, repr(self.b))
        self.t.on("GET", "/v2/account", (401, {"message": "unauthorized"}))
        with self.assertRaises(BrokerError) as ctx:
            self.b.cash_cents()
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertEqual(len(self.t.calls), 1)  # 401 is not retried

    def test_sends_auth_headers(self):
        self.t.on("GET", "/v2/clock", (200, {"is_open": True}))
        self.b.market_open("")
        headers = self.t.calls[0][2]
        self.assertEqual(headers["APCA-API-KEY-ID"], KEY)
        self.assertEqual(headers["APCA-API-SECRET-KEY"], SECRET)

    # -- reads ---------------------------------------------------------------------------

    def test_cash_uses_cash_not_buying_power(self):
        self.t.on("GET", "/v2/account", (200, {"status": "ACTIVE", "cash": "123.459",
                                               "buying_power": "9999.00"}))
        self.assertEqual(self.b.cash_cents(), 12_345)  # rounded down

    def test_blocked_account(self):
        self.t.on("GET", "/v2/account", (200, {"status": "ACTIVE", "cash": "100", "trading_blocked": True}))
        with self.assertRaises(BrokerError):
            self.b.cash_cents()

    def test_asset(self):
        self.t.on("GET", "/v2/assets/VTI", (200, {"tradable": True, "fractionable": True, "status": "active"}))
        self.t.on("GET", "/v2/assets/XYZ", (200, {"tradable": True, "fractionable": False, "status": "active"}))
        self.assertEqual(self.b.asset("VTI"), {"tradable": True, "fractionable": True})
        self.assertEqual(self.b.asset("XYZ"), {"tradable": True, "fractionable": False})
        self.assertIsNone(self.b.asset("NOPE"))  # 404

    def test_inactive_asset_is_not_tradable(self):
        self.t.on("GET", "/v2/assets/OLD", (200, {"tradable": True, "fractionable": True, "status": "inactive"}))
        self.assertFalse(self.b.asset("OLD")["tradable"])

    def test_gets_retry_rate_limits_and_network_errors(self):
        self.t.on("GET", "/v2/clock", (429, {}, {"Retry-After": "3"}), _NetworkError("timeout"),
                  (503, {}), (200, {"is_open": True}))
        self.assertTrue(self.b.market_open(""))
        self.assertEqual(len(self.t.calls), 4)
        self.assertGreaterEqual(self.sleeps[0], 3)  # honours Retry-After

    def test_gets_give_up(self):
        self.t.on("GET", "/v2/clock", (503, {}))
        with self.assertRaises(BrokerError):
            self.b.market_open("")
        self.assertEqual(len(self.t.calls), 4)

    def test_list_orders_pages_and_parses(self):
        page1 = [order_json(f"spare-{i}", submitted=f"2026-03-02T15:00:{i % 60:02d}Z") for i in range(500)]
        page2 = [order_json("spare-last")]
        self.t.on("GET", "/v2/orders", (200, page1), (200, page2))
        orders = self.b.list_orders(since="2026-03-01")
        self.assertEqual(len(orders), 501)
        self.assertIn("after=2026-03-01T00%3A00%3A00Z", self.t.calls[0][1])
        self.assertIn("status=all", self.t.calls[0][1])
        o = orders[-1]
        self.assertEqual((o.client_order_id, o.notional_cents, o.filled_qty, o.filled_avg_price,
                          o.submitted_date), ("spare-last", 1_750, "0.056451613", "310.00", "2026-03-02"))

    def test_order_by_qty_gets_estimated_notional(self):
        self.t.on("GET", "/v2/orders", (200, [order_json("x", notional=None, qty="2", price="10.505")]))
        self.assertEqual(self.b.list_orders()[0].notional_cents, 2_101)

    # -- orders --------------------------------------------------------------------------

    def test_submit_notional_buy(self):
        self.t.on("POST", "/v2/orders", (200, order_json("spare-abc", status="accepted", qty="0", price=None)))
        order = self.b.submit_notional_buy("VTI", 1_750, "spare-abc", "2026-03-02")
        self.assertEqual(order.status, "accepted")
        sent = json.loads(self.t.calls[0][3])
        self.assertEqual(sent, {"symbol": "VTI", "notional": "17.50", "side": "buy", "type": "market",
                                "time_in_force": "day", "client_order_id": "spare-abc"})

    def test_duplicate_client_order_id(self):
        self.t.on("POST", "/v2/orders", (422, {"message": "client_order_id must be unique"}))
        with self.assertRaises(DuplicateOrder):
            self.b.submit_notional_buy("VTI", 500, "spare-abc", "2026-03-02")

    def test_post_is_never_retried_and_unclear_failures_are_looked_up(self):
        self.t.on("POST", "/v2/orders", _NetworkError("timeout"))
        self.t.on("GET", "/v2/orders:by_client_order_id", (200, order_json("spare-abc")))
        order = self.b.submit_notional_buy("VTI", 1_750, "spare-abc", "2026-03-02")
        self.assertEqual(order.client_order_id, "spare-abc")
        self.assertEqual([c[0] for c in self.t.calls].count("POST"), 1)

    def test_unclear_failure_not_found_raises(self):
        self.t.on("POST", "/v2/orders", (502, {}))
        with self.assertRaises(BrokerError):
            self.b.submit_notional_buy("VTI", 1_750, "spare-abc", "2026-03-02")
        self.assertEqual([c[0] for c in self.t.calls].count("POST"), 1)

    def test_insufficient_buying_power(self):
        self.t.on("POST", "/v2/orders", (403, {"message": "insufficient buying power"}))
        with self.assertRaisesRegex(BrokerError, "buying power"):
            self.b.submit_notional_buy("VTI", 1_750, "spare-abc", "2026-03-02")

    def test_money_conversions(self):
        self.assertEqual(cents_to_notional(1_750), "17.50")
        self.assertEqual(cents_to_notional(105), "1.05")
        for bad in (0, -5, 1.5, True):
            with self.assertRaises(ValueError):
                cents_to_notional(bad)
        self.assertEqual(dollars_to_cents("0.019"), 1)
        self.assertEqual(dollars_to_cents(None), 0)
        with self.assertRaises(BrokerError):
            dollars_to_cents("abc")


class AlpacaInvestTests(TempDirCase):
    """The invest run end to end against the Alpaca adapter and a fake transport."""

    def test_weekly_buy_through_alpaca(self):
        t = FakeTransport()
        t.on("GET", "/v2/account", (200, {"status": "ACTIVE", "cash": "500.00"}))
        t.on("GET", "/v2/clock", (200, {"is_open": True}))
        for s in ("VTI", "VXUS", "BND", "SGOV", "VNQ"):
            t.on("GET", f"/v2/assets/{s}", (200, {"tradable": True, "fractionable": True, "status": "active"}))
        t.on("GET", "/v2/orders", (200, [order_json("manual-1", symbol="TSLA")]))
        t.on("POST", "/v2/orders", (200, order_json("spare-x", status="accepted", qty="0", price=None)))
        broker = AlpacaBroker(KEY, SECRET, transport=t, sleep=lambda s: None)

        from spare.core.engine import Transaction

        class Bank:
            name = "bank"

            def transactions(self, since, until):
                return [Transaction(f"t{i}", "2026-03-01", "shop", 101) for i in range(8)]

        targets = {"VTI": 0.35, "VXUS": 0.20, "BND": 0.20, "SGOV": 0.15, "VNQ": 0.10}
        r = invest.run(from_dict({"dry_run": False}), self.ledger(), Bank(), broker, targets,
                       UNIVERSE, "2026-03-02")
        self.assertEqual(r.status, "bought")
        posts = [json.loads(c[3]) for c in t.calls if c[0] == "POST"]
        self.assertEqual(sum(int(p["notional"].replace(".", "")) for p in posts), 792)
        self.assertTrue(all(p["client_order_id"].startswith("spare-") for p in posts))
        self.assertTrue(any("unknown order" in e for e in r.errors))


class PaperOrderTests(unittest.TestCase):
    def test_paper_order_round_trip(self):
        t = FakeTransport()
        t.on("GET", "/v2/assets/VTI", (200, {"tradable": True, "fractionable": True, "status": "active"}))
        t.on("GET", "/v2/clock", (200, {"is_open": True}))
        b = AlpacaBroker(KEY, SECRET, transport=t, sleep=lambda s: None)
        placed = {}

        def post(method, url, headers, body):
            placed["coid"] = json.loads(body)["client_order_id"]
            return Response(200, json.dumps(order_json(placed["coid"], notional="5.00")).encode(), {})

        real = t.__call__

        def transport(method, url, headers, body):
            if method == "POST":
                return post(method, url, headers, body)
            if "/v2/orders" in url and placed:
                payload = order_json(placed["coid"], notional="5.00")
                return Response(200, json.dumps(payload if "by_client" in url else [payload]).encode(), {})
            return real(method, url, headers, body)

        b._transport = transport
        lines = []
        self.assertEqual(alpaca_tools.paper_order(b, "VTI", 500, out=lines.append), 0)
        self.assertTrue(placed["coid"].startswith(invest.TEST_ORDER_PREFIX))
        self.assertTrue(any("Found again" in line and "yes" in line for line in lines))

    def test_paper_order_refuses_live_and_big_amounts(self):
        live = AlpacaBroker(KEY, SECRET, env="live", allow_live=True, transport=FakeTransport())
        with self.assertRaises(SystemExit):
            alpaca_tools.paper_order(live, "VTI", 500, out=lambda *_: None)
        paper = AlpacaBroker(KEY, SECRET, transport=FakeTransport())
        with self.assertRaises(SystemExit):
            alpaca_tools.paper_order(paper, "VTI", 10_000, out=lambda *_: None)

    def test_test_orders_are_ignored_by_reconcile(self):
        from spare.core.broker import Order
        from spare.fake import FakeBroker
        import os, tempfile
        from spare.core.ledger import Ledger
        broker = FakeBroker()
        broker.orders["spare-test-1"] = Order("spare-test-1", "VTI", 500, "filled", "0.01", "310", "2026-03-02")
        with tempfile.TemporaryDirectory() as d:
            led = Ledger(os.path.join(d, "l.jsonl"))
            alerts = invest.reconcile(led, broker, "2026-03-02")
            self.assertEqual(alerts, [])
            self.assertEqual(led.events, [])


if __name__ == "__main__":
    unittest.main()
