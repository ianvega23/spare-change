from spare.core import engine, invest
from spare.core.broker import Order
from spare.core.config import from_dict
from spare.core.engine import Transaction
from spare.fake import FakeBroker

from tests.helpers import UNIVERSE, TempDirCase

TARGETS = {"VTI": 0.35, "VXUS": 0.20, "BND": 0.20, "SGOV": 0.15, "VNQ": 0.10}
MONDAY = "2026-03-02"
LIVE = from_dict({"dry_run": False})


class ListBank:
    name = "list-bank"

    def __init__(self, txns):
        self.txns = list(txns)

    def transactions(self, since, until):
        return [t for t in self.txns if since <= t.date <= until]


def purchases(n, cents=101, day="2026-03-01", prefix="t"):
    """n purchases of $1.01: 99 cents of round-up each."""
    return [Transaction(f"{prefix}{i}", day, "shop", cents) for i in range(n)]


class InvestTests(TempDirCase):
    def run_once(self, bank, broker, cfg=LIVE, targets=TARGETS, day=MONDAY):
        return invest.run(cfg, self.ledger(), bank, broker, targets, UNIVERSE, day)

    def test_below_trigger_buys_nothing(self):
        broker = FakeBroker()
        r = self.run_once(ListBank(purchases(50)), broker)  # $49.50
        self.assertEqual(r.status, "below_trigger")
        self.assertEqual(r.pot_cents, 4_950)
        self.assertEqual(broker.orders, {})

    def test_buys_at_trigger(self):
        broker = FakeBroker()
        r = self.run_once(ListBank(purchases(51)), broker)  # $50.49
        self.assertEqual(r.status, "bought")
        self.assertEqual(sum(o.notional_cents for o in r.orders), 5_049)
        self.assertEqual(r.pot_cents, 0)
        self.assertEqual(len(broker.orders), 5)

    def test_credits_pending_and_duplicates_ignored(self):
        txns = purchases(3) + [Transaction("refund", "2026-03-01", "refund", -2_000),
                               Transaction("pend", "2026-03-01", "shop", 101, posted=False)]
        bank = ListBank(txns + purchases(3))  # the same ids twice
        r = self.run_once(bank, FakeBroker())
        self.assertEqual(r.new_roundups, 3)
        self.assertEqual(r.pot_cents, 297)
        r = self.run_once(bank, FakeBroker())
        self.assertEqual(r.new_roundups, 0)

    def test_pending_counts_once_posted(self):
        bank = ListBank([Transaction("p", "2026-03-01", "shop", 425, posted=False)])
        self.assertEqual(self.run_once(bank, FakeBroker()).pot_cents, 0)
        bank.txns = [Transaction("p", "2026-03-01", "shop", 450)]
        self.assertEqual(self.run_once(bank, FakeBroker()).pot_cents, 50)  # posted amount

    def test_kill_switch(self):
        broker = FakeBroker()
        r = self.run_once(ListBank(purchases(60)), broker, cfg=from_dict({"enabled": False}))
        self.assertEqual(r.status, "disabled")
        self.assertEqual(self.ledger().events, [])
        self.assertEqual(broker.orders, {})

    def test_dry_run_records_roundups_but_places_nothing(self):
        broker = FakeBroker()
        r = self.run_once(ListBank(purchases(60)), broker, cfg=from_dict({}))
        self.assertEqual(r.status, "dry_run")
        self.assertEqual(sum(r.plan.values()), 5_940)
        self.assertEqual(broker.orders, {})
        self.assertEqual(self.ledger().pot_cents(), 5_940)

    def test_market_closed(self):
        r = self.run_once(ListBank(purchases(60)), FakeBroker(), day="2026-03-07")  # Saturday
        self.assertEqual(r.status, "market_closed")

    def test_cash_short_leaves_pot(self):
        broker = FakeBroker(cash_cents=1_000)
        r = self.run_once(ListBank(purchases(60)), broker)
        self.assertEqual(r.status, "cash_short")
        self.assertEqual(r.pot_cents, 5_940)
        self.assertEqual(broker.orders, {})
        self.assertTrue(any("top up" in e.get("message", "") for e in self.ledger().of_type("alert")))

    def test_monthly_cap_clamps_and_carries(self):
        cfg = from_dict({"dry_run": False, "monthly_cap": 60})
        broker = FakeBroker()
        bank = ListBank(purchases(122))  # $120.78
        r = self.run_once(bank, broker, cfg=cfg)
        self.assertEqual(sum(o.notional_cents for o in r.orders), 6_000)
        self.assertEqual(r.pot_cents, 6_078)  # the rest stays in the pot
        r = self.run_once(bank, broker, cfg=cfg, day="2026-03-03")
        self.assertEqual(r.status, "cap_reached")
        r = self.run_once(bank, broker, cfg=cfg, day="2026-04-01")  # new month
        self.assertEqual(r.status, "bought")
        self.assertEqual(r.pot_cents, 78)

    def test_invalid_targets_block_the_buy(self):
        broker = FakeBroker()
        for bad in ({"VTI": 1.0}, {**TARGETS, "VNQ": 0.0, "TSLA": 0.10}, {}):
            r = self.run_once(ListBank(purchases(60)), broker, targets=bad)
            self.assertEqual(r.status, "invalid", bad)
        self.assertEqual(broker.orders, {})

    def test_untradable_symbol_blocks_the_buy(self):
        broker = FakeBroker(untradable={"VNQ"})
        r = self.run_once(ListBank(purchases(60)), broker)
        self.assertEqual(r.status, "invalid")
        self.assertEqual(broker.orders, {})

    def test_same_pot_twice_orders_once(self):
        broker = FakeBroker()
        bank = ListBank(purchases(60))
        self.run_once(bank, broker)
        r = self.run_once(bank, broker)
        self.assertEqual(r.status, "below_trigger")
        self.assertEqual(len(broker.orders), 5)

    def test_crash_after_ordering_is_reconciled_not_repeated(self):
        """Orders went through but the ledger wasn't saved: the retry must not buy again."""
        broker = FakeBroker()
        bank = ListBank(purchases(60))
        led = self.ledger()
        invest.collect_roundups(led, bank, LIVE, MONDAY)
        batch = engine.batch_id(led.last_batch_id(), led.unbatched_roundup_ids())
        plan = engine.split(led.pot_cents(), TARGETS)
        for symbol, cents in plan.items():  # orders placed, then the job died
            broker.submit_notional_buy(symbol, cents, engine.client_order_id(batch, symbol), MONDAY)
        r = self.run_once(bank, broker)
        self.assertEqual(len(broker.orders), 5)
        self.assertEqual(r.pot_cents, 0)
        self.assertEqual(r.status, "below_trigger")
        self.assertEqual(len(self.ledger().of_type("reconciled")), 5)

    def test_unknown_order_alert(self):
        broker = FakeBroker()
        broker.orders["someone-else"] = Order("someone-else", "TSLA", 100_000, "filled")
        r = self.run_once(ListBank(purchases(3)), broker)
        self.assertTrue(any("unknown order" in e for e in r.errors))
        self.assertEqual(self.ledger().pot_cents(), 297)  # not counted as ours

    def test_qty_has_no_floats(self):
        self.assertEqual(invest.qty_from(1_750, 31_000), "0.056452")
