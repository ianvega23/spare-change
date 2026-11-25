from spare.core import engine, invest
from spare.core.broker import Order
from spare.core.config import from_dict
from spare.core.engine import Transaction
from spare.fake import FakeBroker

from tests.helpers import UNIVERSE, TempDirCase

TARGETS = {"VTI": 0.35, "VXUS": 0.20, "BND": 0.20, "SGOV": 0.15, "VNQ": 0.10}
MONDAY = "2026-03-02"
TUESDAY = "2026-03-03"
NEXT_MONDAY = "2026-03-09"
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

    def test_no_buy_before_the_buy_day(self):
        broker = FakeBroker()
        cfg = from_dict({"dry_run": False, "buy_day": "friday"})
        r = self.run_once(ListBank(purchases(60)), broker, cfg=cfg)  # a Monday
        self.assertEqual(r.status, "not_buy_day")
        self.assertEqual(r.pot_cents, 5_940)
        self.assertEqual(broker.orders, {})

    def test_buys_the_whole_week_on_buy_day_whatever_the_amount(self):
        broker = FakeBroker()
        r = self.run_once(ListBank(purchases(8)), broker)  # $7.92
        self.assertEqual(r.status, "bought")
        self.assertEqual(sum(o.notional_cents for o in r.orders), 792)
        self.assertEqual(r.pot_cents, 0)
        self.assertTrue(all(o.notional_cents >= 100 for o in r.orders))

    def test_once_a_week(self):
        broker = FakeBroker()
        bank = ListBank(purchases(8))
        self.run_once(bank, broker)
        bank.txns += purchases(8, day="2026-03-03", prefix="tue")
        r = self.run_once(bank, broker, day=TUESDAY)
        self.assertEqual(r.status, "not_buy_day")
        self.assertEqual(r.pot_cents, 792)  # waits for next week
        r = self.run_once(bank, broker, day=NEXT_MONDAY)
        self.assertEqual(r.status, "bought")
        self.assertEqual(r.pot_cents, 0)

    def test_holiday_monday_buys_on_tuesday(self):
        broker = FakeBroker()
        broker.market_open = lambda day: day != MONDAY
        bank = ListBank(purchases(8))
        self.assertEqual(self.run_once(bank, broker).status, "market_closed")
        self.assertEqual(self.run_once(bank, broker, day=TUESDAY).status, "bought")

    def test_pot_under_one_dollar_carries_over(self):
        r = self.run_once(ListBank(purchases(1)), FakeBroker())  # 99 cents
        self.assertEqual(r.status, "pot_too_small")
        self.assertEqual(r.pot_cents, 99)

    def test_credits_pending_and_duplicates_ignored(self):
        txns = purchases(3) + [Transaction("refund", "2026-03-01", "refund", -2_000),
                               Transaction("pend", "2026-03-01", "shop", 101, posted=False)]
        bank = ListBank(txns + purchases(3))  # the same ids twice
        r = self.run_once(bank, FakeBroker(), cfg=from_dict({}))  # dry run keeps the pot
        self.assertEqual(r.new_roundups, 3)
        self.assertEqual(r.pot_cents, 297)
        r = self.run_once(bank, FakeBroker(), cfg=from_dict({}))
        self.assertEqual(r.new_roundups, 0)

    def test_pending_counts_once_posted(self):
        bank = ListBank([Transaction("p", "2026-03-01", "shop", 425, posted=False)])
        dry = from_dict({})
        self.assertEqual(self.run_once(bank, FakeBroker(), cfg=dry).pot_cents, 0)
        bank.txns = [Transaction("p", "2026-03-01", "shop", 450)]
        self.assertEqual(self.run_once(bank, FakeBroker(), cfg=dry).pot_cents, 50)  # posted amount

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

    def test_never_buys_on_a_weekend(self):
        r = self.run_once(ListBank(purchases(60)), FakeBroker(), day="2026-03-07")  # Saturday
        self.assertEqual(r.status, "not_buy_day")

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
        r = self.run_once(bank, broker, cfg=cfg, day=NEXT_MONDAY)
        self.assertEqual(r.status, "cap_reached")
        r = self.run_once(bank, broker, cfg=cfg, day="2026-04-06")  # first Monday of April
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
        self.assertEqual(r.status, "not_buy_day")
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
        self.assertEqual(r.status, "not_buy_day")  # the reconciled orders were this week's buy
        self.assertEqual(len(self.ledger().of_type("reconciled")), 5)

    def test_unknown_order_alert(self):
        broker = FakeBroker()
        broker.orders["someone-else"] = Order("someone-else", "TSLA", 100_000, "filled",
                                              submitted_date=MONDAY)
        r = self.run_once(ListBank(purchases(3)), broker, cfg=from_dict({}))
        self.assertTrue(any("unknown order" in e for e in r.errors))
        self.assertEqual(self.ledger().pot_cents(), 297)  # not counted as ours
        r = self.run_once(ListBank(purchases(3)), broker, cfg=from_dict({}))
        self.assertFalse(any("unknown order" in e for e in r.errors))  # alerted once, not every run

    def test_order_that_never_filled_gives_money_back(self):
        broker = FakeBroker()
        bank = ListBank(purchases(8))
        self.run_once(bank, broker)
        coid = next(iter(broker.orders))
        o = broker.orders[coid]
        broker.orders[coid] = Order(o.client_order_id, o.symbol, o.notional_cents, "canceled",
                                    "0", None, o.submitted_date)
        r = self.run_once(bank, broker, day=TUESDAY)
        self.assertEqual(r.pot_cents, o.notional_cents)
        self.assertTrue(any("canceled" in e for e in r.errors))
        self.assertEqual(self.ledger().voided_order_ids(), {coid})
        r = self.run_once(bank, broker, day=NEXT_MONDAY)  # voided once, then rebought
        self.assertEqual(r.status, "bought")
        self.assertEqual(r.pot_cents, 0)

    def test_dead_order_missing_from_ledger_is_not_recorded(self):
        broker = FakeBroker()
        broker.orders["spare-dead"] = Order("spare-dead", "VTI", 500, "rejected", "0", None, MONDAY)
        r = self.run_once(ListBank(purchases(3)), broker, cfg=from_dict({}))
        self.assertEqual(r.pot_cents, 297)
        self.assertEqual(self.ledger().of_type("buy"), [])

    def test_qty_has_no_floats(self):
        self.assertEqual(invest.qty_from(1_750, 31_000), "0.056451613")
