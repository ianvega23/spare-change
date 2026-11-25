"""The Public adapter against a scripted fake transport. No network: Public has no paper account."""
import json
import unittest
import uuid

from spare import jobs
from spare.core import engine, invest
from spare.core.broker import BrokerError
from spare.core.broker_public import (PublicBroker, client_id_from_uuid, order_uuid)
from spare.core.config import ConfigError, from_dict
from spare.core.http import NetworkError, Response
from spare.core.state import State
from tests.helpers import UNIVERSE, TempDirCase
from tests.test_broker_alpaca import FakeTransport as _AlpacaTransport

SECRET = "public-secret-do-not-leak"
TOKEN = "token-do-not-leak"
ACCT = "5LF05438"
BASE = "/userapigateway/trading"
COID = engine.client_order_id("a" * 32, "VTI")


class FakeTransport(_AlpacaTransport):
    def __call__(self, method, url, headers, body):
        path = url.split("api.public.com", 1)[1].split("?", 1)[0]
        self.calls.append((method, url, headers, body))
        queue = self.routes.get((method, path))
        if not queue:
            return Response(404, b'{"message":"not found"}', {})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return Response(item[0], json.dumps(item[1]).encode(), item[2] if len(item) > 2 else {})


def order_json(coid=COID, symbol="VTI", status="FILLED", notional="17.50", qty="0.0564",
               price="310.00"):
    return {"orderId": order_uuid(coid), "instrument": {"symbol": symbol, "type": "EQUITY"},
            "status": status, "notionalValue": notional, "filledQuantity": qty,
            "averagePrice": price, "createdAt": "2026-03-02T15:00:01Z", "side": "BUY"}


class PublicTests(unittest.TestCase):
    def setUp(self):
        self.t = FakeTransport()
        self.t.on("POST", "/userapiauthservice/personal/access-tokens", (200, {"accessToken": TOKEN}))
        self.t.on("GET", f"{BASE}/account", (200, {"accounts": [
            {"accountId": "IRA1", "accountType": "IRA"},
            {"accountId": ACCT, "accountType": "BROKERAGE", "brokerageAccountType": "CASH"}]}))
        self.sleeps = []
        self.now = [1_000_000.0]
        self.b = self.make(allow_orders=True)

    def make(self, **kw):
        return PublicBroker(SECRET, transport=self.t, sleep=self.sleeps.append,
                            clock=lambda: self.now[0], **kw)

    def bodies(self, method, suffix):
        return [json.loads(c[3]) for c in self.t.calls if c[0] == method and c[1].endswith(suffix)]

    # -- ids --------------------------------------------------------------------------------

    def test_order_ids_are_valid_uuids_that_map_back(self):
        oid = order_uuid(COID)
        self.assertEqual(uuid.UUID(oid).version, 4)
        self.assertEqual(client_id_from_uuid(oid), COID)
        self.assertEqual(client_id_from_uuid("0b0e5e4a-1111-4111-8111-111111111111"),
                         "0b0e5e4a-1111-4111-8111-111111111111")  # not ours: unchanged
        with self.assertRaises(ValueError):
            order_uuid("someone-else")

    # -- safety -----------------------------------------------------------------------------

    def test_orders_locked_by_default_but_reads_work(self):
        b = self.make()
        self.t.on("GET", f"{BASE}/{ACCT}/portfolio/v2", (200, {"buyingPower": {"cashOnlyBuyingPower": "42.10"}}))
        self.assertEqual(b.cash_cents(), 4210)
        with self.assertRaisesRegex(BrokerError, "no paper trading"):
            b.submit_notional_buy("VTI", 1000, COID, "2026-03-02")
        self.assertEqual(self.bodies("POST", "/order"), [])

    def test_secret_and_token_never_leak(self):
        self.t.on("GET", f"{BASE}/{ACCT}/portfolio/v2", (403, {"message": "forbidden"}))
        with self.assertRaises(BrokerError) as e:
            self.b.cash_cents()
        for text in (str(e.exception), repr(self.b)):
            self.assertNotIn(SECRET, text)
            self.assertNotIn(TOKEN, text)

    def test_five_dollar_minimum(self):
        self.assertEqual(self.b.min_order_cents, 500)
        with self.assertRaisesRegex(BrokerError, r"\$5 minimum"):
            self.b.submit_notional_buy("VTI", 499, COID, "2026-03-02")

    # -- auth and account -------------------------------------------------------------------

    def test_token_cached_then_refreshed_before_expiry(self):
        self.t.on("GET", f"{BASE}/{ACCT}/portfolio/v2", (200, {"buyingPower": {"cashOnlyBuyingPower": "1"}}))
        self.b.cash_cents()
        self.b.cash_cents()
        auth = self.bodies("POST", "/access-tokens")
        self.assertEqual(len(auth), 1)
        self.assertEqual(auth[0]["secret"], SECRET)
        self.now[0] += 59 * 60  # inside the 2-minute safety margin
        self.b.cash_cents()
        self.assertEqual(len(self.bodies("POST", "/access-tokens")), 2)
        sent = [c[2]["Authorization"] for c in self.t.calls if "portfolio" in c[1]]
        self.assertEqual(set(sent), {f"Bearer {TOKEN}"})

    def test_picks_the_one_brokerage_account(self):
        self.assertEqual(self.b.account_id(), ACCT)
        self.t.on("GET", f"{BASE}/account", (200, {"accounts": [
            {"accountId": "A", "accountType": "BROKERAGE"}, {"accountId": "B", "accountType": "BROKERAGE"}]}))
        with self.assertRaisesRegex(BrokerError, "public_account_id"):
            self.make().account_id()
        self.assertEqual(self.make(account_id="B").account_id(), "B")

    # -- reads ------------------------------------------------------------------------------

    def test_retries_rate_limits_and_server_errors_on_reads(self):
        self.t.on("GET", f"{BASE}/{ACCT}/portfolio/v2", (429, {}, {"Retry-After": "3"}),
                  (503, {}), NetworkError("reset"), (200, {"buyingPower": {"cashOnlyBuyingPower": "5.00"}}))
        self.assertEqual(self.b.cash_cents(), 500)
        self.assertEqual(len(self.sleeps), 3)
        self.assertGreaterEqual(self.sleeps[0], 3)

    def test_asset_tradability(self):
        path = f"{BASE}/instruments/VTI/EQUITY"
        self.t.on("GET", path, (200, {"trading": "BUY_AND_SELL", "fractionalTrading": "BUY_AND_SELL"}))
        self.assertEqual(self.b.asset("VTI"), {"tradable": True, "fractionable": True})
        self.t.on("GET", path, (200, {"trading": "LIQUIDATION_ONLY", "fractionalTrading": "DISABLED"}))
        self.assertEqual(self.b.asset("VTI"), {"tradable": False, "fractionable": False})
        self.assertIsNone(self.b.asset("NOPE"))

    def test_positions_and_prices(self):
        self.t.on("GET", f"{BASE}/{ACCT}/portfolio/v2", (200, {"positions": [
            {"instrument": {"symbol": "VTI"}, "quantity": "0.1", "currentValue": "31.07",
             "lastPrice": {"lastPrice": "310.70"}}]}))
        self.assertEqual(self.b.positions_cents(), {"VTI": 3107})
        self.assertEqual(self.b.latest_price("VTI"), "310.70")
        self.assertIsNone(self.b.latest_price("BND"))

    def test_list_orders_maps_statuses_and_ids(self):
        self.t.on("POST", f"{BASE}/{ACCT}/order/search", (200, {"orders": [
            order_json(status="QUEUED_CANCELLED", qty="0", notional=None),
            {**order_json(), "orderId": "0b0e5e4a-1111-4111-8111-111111111111"}]}))
        ours, theirs = self.b.list_orders(since="2026-02-01")
        self.assertEqual(self.bodies("POST", "/order/search")[0], {"createdAfter": "2026-02-01T00:00:00Z"})
        self.assertEqual((ours.client_order_id, ours.status, ours.dead), (COID, "canceled", True))
        self.assertEqual(ours.submitted_date, "2026-03-02")
        self.assertFalse(theirs.client_order_id.startswith("spare-"))  # reconcile flags it

    # -- orders -----------------------------------------------------------------------------

    def test_submit_sends_a_cash_market_day_order(self):
        self.t.on("POST", f"{BASE}/{ACCT}/order", (200, {"orderId": order_uuid(COID)}))
        self.t.on("GET", f"{BASE}/{ACCT}/order/{order_uuid(COID)}", (200, order_json(status="NEW", qty="0")))
        order = self.b.submit_notional_buy("VTI", 1750, COID, "2026-03-02")
        body = self.bodies("POST", "/order")[0]
        self.assertEqual(body, {"orderId": order_uuid(COID), "instrument": {"symbol": "VTI", "type": "EQUITY"},
                                "orderSide": "BUY", "orderType": "MARKET",
                                "expiration": {"timeInForce": "DAY"}, "amount": "17.50", "useMargin": False})
        self.assertEqual((order.client_order_id, order.status, order.notional_cents), (COID, "new", 1750))

    def test_async_placement_not_visible_yet(self):
        self.t.on("POST", f"{BASE}/{ACCT}/order", (200, {"orderId": order_uuid(COID)}))
        order = self.b.submit_notional_buy("VTI", 1750, COID, "2026-03-02")
        self.assertEqual((order.status, order.notional_cents, order.submitted_date), ("new", 1750, "2026-03-02"))

    def test_unclear_failure_finds_the_order_instead_of_resending(self):
        self.t.on("POST", f"{BASE}/{ACCT}/order", NetworkError("timeout"))
        self.t.on("GET", f"{BASE}/{ACCT}/order/{order_uuid(COID)}", (200, order_json()))
        order = self.b.submit_notional_buy("VTI", 1750, COID, "2026-03-02")
        self.assertEqual(len(self.bodies("POST", "/order")), 1)
        self.assertEqual(order.status, "filled")

    def test_unclear_failure_resends_the_same_id_once(self):
        self.t.on("POST", f"{BASE}/{ACCT}/order", (502, {}), (200, {"orderId": order_uuid(COID)}))
        self.b.submit_notional_buy("VTI", 1750, COID, "2026-03-02")
        sent = self.bodies("POST", "/order")
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0], sent[1])  # identical body: Public drops the duplicate

    def test_a_refused_order_is_not_resent(self):
        self.t.on("POST", f"{BASE}/{ACCT}/order", (400, {"message": "insufficient buying power"}))
        with self.assertRaisesRegex(BrokerError, "refused"):
            self.b.submit_notional_buy("VTI", 1750, COID, "2026-03-02")
        self.assertEqual(len(self.bodies("POST", "/order")), 1)


class MinimumOrderTests(TempDirCase):
    """A broker's own minimum ($5 on Public) flows through the split, the guard and the run."""

    def test_engine_never_goes_below_one_dollar(self):
        self.assertEqual(engine.buy_amount(400, 20000, 0, 500), 0)
        self.assertEqual(engine.buy_amount(600, 20000, 0, 500), 600)
        self.assertEqual(engine.buy_amount(150, 20000, 0, 50), 150)  # can't lower Alpaca's $1
        self.assertEqual(engine.buy_amount(50, 20000, 0, 50), 0)
        plan = engine.split(1200, {"VTI": 0.6, "BND": 0.3, "VXUS": 0.1}, None, 500)
        self.assertEqual(sum(plan.values()), 1200)
        self.assertTrue(all(c >= 500 for c in plan.values()), plan)

    def test_run_waits_for_five_dollars_with_public(self):
        from spare.fake import FakeBroker
        from tests.test_invest import TARGETS, ListBank, purchases
        cfg = from_dict({"dry_run": False})
        broker = FakeBroker(cash_cents=100000)
        broker.min_order_cents = 500
        ledger = self.ledger()
        r = invest.run(cfg, ledger, ListBank(purchases(4)), broker, TARGETS,
                       UNIVERSE, "2026-03-02")  # $3.96 on a Monday
        self.assertEqual(r.status, "pot_too_small")
        self.assertEqual(broker.orders, {})
        r = invest.run(cfg, ledger, ListBank(purchases(6, prefix="u")), broker,
                       TARGETS, UNIVERSE, "2026-03-02")  # now $9.90
        self.assertEqual(r.status, "bought", r.errors)
        self.assertEqual(sum(r.plan.values()), 990)
        self.assertTrue(all(c >= 500 for c in r.plan.values()), r.plan)  # no slice under $5


class ConfigAndJobTests(TempDirCase):
    def test_broker_setting(self):
        self.assertEqual(from_dict({}).broker, "alpaca")
        self.assertEqual(from_dict({"broker": "public", "public_account_id": "X"}).public_account_id, "X")
        with self.assertRaises(ConfigError):
            from_dict({"broker": "robinhood"})

    def test_public_is_live_so_orders_need_the_gate(self):
        state = State(self.dir).ensure()
        env = {"PUBLIC_SECRET_KEY": SECRET}
        dry = jobs.broker_from_env(from_dict({"broker": "public"}), state, env)
        self.assertIsInstance(dry, PublicBroker)
        self.assertFalse(dry.allow_orders)  # dry run can read, never order
        with self.assertRaisesRegex(SystemExit, "go-live"):
            jobs.broker_from_env(from_dict({"broker": "public", "dry_run": False}), state, env)
        state._write_json("go_live.json", {"passed": True})
        live = jobs.broker_from_env(from_dict({"broker": "public", "dry_run": False}), state, env)
        self.assertTrue(live.allow_orders)
        with self.assertRaisesRegex(SystemExit, "PUBLIC_SECRET_KEY"):
            jobs.broker_from_env(from_dict({"broker": "public"}), state, {})

    def test_doctor_checks_public_secret(self):
        state = State(self.dir).ensure()
        state.config_path.write_text('broker = "public"\nbank = "plaid"\n')
        out = []
        jobs.doctor(self.dir, environ={"PLAID_CLIENT_ID": "x", "PLAID_SECRET": "x",
                                       "PLAID_ACCESS_TOKEN": "x"}, out=out.append)
        self.assertIn("FAIL  secrets: PUBLIC_SECRET_KEY not set", out)
        self.assertNotIn("APCA", "\n".join(out))


if __name__ == "__main__":
    unittest.main()
