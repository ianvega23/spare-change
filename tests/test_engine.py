import unittest

from spare.core import engine
from spare.core.engine import Transaction, buy_amount, round_up_cents, split

TARGETS = {"VTI": 0.35, "VXUS": 0.20, "BND": 0.20, "SGOV": 0.15, "VNQ": 0.10}


class RoundUp(unittest.TestCase):
    def test_edges(self):
        self.assertEqual(round_up_cents(425), 75)
        self.assertEqual(round_up_cents(500), 0)
        self.assertEqual(round_up_cents(1001), 99)
        self.assertEqual(round_up_cents(1), 99)
        self.assertEqual(round_up_cents(99), 1)

    def test_credits_and_zero_give_nothing(self):
        self.assertEqual(round_up_cents(0), 0)
        self.assertEqual(round_up_cents(-2399), 0)

    def test_multiplier(self):
        t = Transaction("a", "2026-01-05", "x", 425)
        self.assertEqual(engine.roundup_for(t, 1), 75)
        self.assertEqual(engine.roundup_for(t, 2), 150)
        self.assertEqual(engine.roundup_for(t, 3), 225)

    def test_pending_counts_nothing(self):
        t = Transaction("a", "2026-01-05", "x", 425, posted=False)
        self.assertEqual(engine.roundup_for(t, 2), 0)


class Trigger(unittest.TestCase):
    def test_below_trigger_buys_nothing(self):
        self.assertEqual(buy_amount(4_999, 5_000, 20_000, 0), 0)

    def test_at_trigger_buys_whole_pot(self):
        self.assertEqual(buy_amount(5_000, 5_000, 20_000, 0), 5_000)
        self.assertEqual(buy_amount(5_437, 5_000, 20_000, 0), 5_437)

    def test_clamped_to_monthly_cap(self):
        self.assertEqual(buy_amount(6_000, 5_000, 20_000, 17_000), 3_000)

    def test_cap_reached(self):
        self.assertEqual(buy_amount(6_000, 5_000, 20_000, 20_000), 0)
        self.assertEqual(buy_amount(6_000, 5_000, 20_000, 19_950), 0)  # under the $1 minimum


class Split(unittest.TestCase):
    def test_proportional_when_empty(self):
        self.assertEqual(split(5_000, TARGETS),
                         {"VTI": 1_750, "VXUS": 1_000, "BND": 1_000, "SGOV": 750, "VNQ": 500})

    def test_always_sums_to_amount(self):
        for amount in (100, 101, 499, 5_001, 5_437, 19_999):
            self.assertEqual(sum(split(amount, TARGETS).values()), amount, amount)

    def test_steers_to_underweight_without_selling(self):
        holdings = {"VTI": 10_000, "VXUS": 0, "BND": 2_000, "SGOV": 1_500, "VNQ": 1_000}
        plan = split(5_000, TARGETS, holdings)
        self.assertNotIn("VTI", plan)  # VTI is already over target
        self.assertGreater(plan["VXUS"], plan["BND"])
        self.assertTrue(all(c > 0 for c in plan.values()))

    def test_no_slice_under_one_dollar(self):
        for amount in (100, 250, 499, 999):
            plan = split(amount, TARGETS)
            self.assertTrue(all(c >= 100 for c in plan.values()), plan)
            self.assertEqual(sum(plan.values()), amount)

    def test_small_slices_go_to_most_underweight(self):
        plan = split(300, TARGETS)
        self.assertEqual(plan, {"VTI": 300})

    def test_under_one_dollar_is_nothing(self):
        self.assertEqual(split(99, TARGETS), {})


class Ids(unittest.TestCase):
    def test_batch_id_is_order_independent_and_chained(self):
        self.assertEqual(engine.batch_id("", ["b", "a"]), engine.batch_id("", ["a", "b"]))
        self.assertNotEqual(engine.batch_id("x", ["a"]), engine.batch_id("y", ["a"]))

    def test_client_order_id(self):
        coid = engine.client_order_id("batch", "VTI")
        self.assertTrue(coid.startswith("spare-"))
        self.assertEqual(len(coid), 6 + 24)
        self.assertEqual(coid, engine.client_order_id("batch", "VTI"))
        self.assertNotEqual(coid, engine.client_order_id("batch", "BND"))


if __name__ == "__main__":
    unittest.main()
