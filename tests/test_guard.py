import unittest
from datetime import datetime, timedelta

from spare.core import guard
from spare.core.guard import Limits, Universe

ETFS = frozenset({"VTI", "VOO", "VXUS", "BND", "SGOV", "VNQ", "VEA", "GLDM"})
STOCKS = frozenset({"AAPL", "MSFT", "NVDA", "JPM"})
U = Universe(etfs=ETFS, stocks=STOCKS)
OK = {"VTI": 0.35, "VXUS": 0.20, "BND": 0.20, "SGOV": 0.15, "VNQ": 0.10}
STOCKY = Limits(allow_stocks=True)


def errs(targets, limits=Limits()):
    return guard.validate_targets(targets, U, limits)


class Targets(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(errs(OK), [])

    def test_universe(self):
        self.assertTrue(errs({**OK, "VNQ": 0.0, "TSLA": 0.10}))
        bad = {"VTI": 0.35, "VXUS": 0.20, "BND": 0.20, "SGOV": 0.15, "TSLA": 0.10}
        self.assertIn("TSLA: not in the allowlist", errs(bad))

    def test_min_holdings_at_limit_and_past(self):
        self.assertEqual(errs(OK), [])  # 5 holdings
        four = {"VTI": 0.35, "VXUS": 0.35, "BND": 0.20, "SGOV": 0.10}
        self.assertTrue(any("at least 5" in e for e in errs(four)))

    def test_etf_cap_at_limit_and_past(self):
        self.assertEqual(errs({"VTI": 0.35, "VXUS": 0.20, "BND": 0.20, "SGOV": 0.15, "VNQ": 0.10}), [])
        over = {"VTI": 0.36, "VXUS": 0.19, "BND": 0.20, "SGOV": 0.15, "VNQ": 0.10}
        self.assertTrue(any("ETF cap" in e for e in errs(over)))

    def test_one_holding_at_100_percent(self):
        self.assertTrue(errs({"VTI": 1.0}))

    def test_stocks_need_opt_in(self):
        t = {"VTI": 0.30, "VXUS": 0.25, "BND": 0.20, "SGOV": 0.15, "AAPL": 0.10}
        self.assertIn("AAPL: individual stocks are not enabled", errs(t))
        self.assertEqual(errs(t, STOCKY), [])

    def test_stock_cap_at_limit_and_past(self):
        t = {"VTI": 0.30, "VXUS": 0.25, "BND": 0.20, "SGOV": 0.14, "AAPL": 0.11}
        self.assertTrue(any("stock cap" in e for e in errs(t, STOCKY)))

    def test_stocks_total_at_limit_and_past(self):
        at = {"VTI": 0.35, "VXUS": 0.35, "AAPL": 0.10, "MSFT": 0.10, "NVDA": 0.10}
        self.assertEqual(errs(at, STOCKY), [])
        past = {"VTI": 0.35, "VXUS": 0.34, "AAPL": 0.10, "MSFT": 0.10, "NVDA": 0.10, "JPM": 0.01}
        self.assertTrue(any("stocks total" in e for e in errs(past, STOCKY)))

    def test_weights_sum(self):
        self.assertTrue(any("sum to" in e for e in errs({**OK, "VNQ": 0.09})))
        self.assertTrue(any("sum to" in e for e in errs({**OK, "VNQ": 0.11})))
        self.assertEqual(errs({**OK, "VNQ": 0.1 + 1e-12}), [])  # float noise is fine

    def test_whole_percents(self):
        t = {"VTI": 0.345, "VXUS": 0.205, "BND": 0.20, "SGOV": 0.15, "VNQ": 0.10}
        self.assertTrue(any("whole percent" in e for e in errs(t)))

    def test_no_negative_or_zero_weights(self):
        t = {"VTI": 0.35, "VXUS": 0.35, "BND": 0.35, "SGOV": 0.05, "VNQ": -0.10}
        self.assertTrue(any("no sells" in e for e in errs(t)))
        self.assertTrue(any("above 0" in e for e in errs({**OK, "VEA": 0})))

    def test_garbage(self):
        self.assertTrue(errs({}))
        self.assertTrue(errs([]))
        self.assertTrue(errs({**OK, "VNQ": "0.10"}))
        self.assertTrue(errs({**OK, "VNQ": True}))


class Classify(unittest.TestCase):
    def test_first_allocation_is_big(self):
        self.assertEqual(guard.classify({}, OK), "big")

    def test_none(self):
        self.assertEqual(guard.classify(OK, dict(OK)), "none")

    def test_small_at_limit_big_past(self):
        at = {**OK, "VTI": 0.25, "VXUS": 0.30}  # 10 points
        self.assertAlmostEqual(guard.shift(OK, at), 0.10)
        self.assertEqual(guard.classify(OK, at), "small")
        past = {**OK, "VTI": 0.24, "VXUS": 0.31}  # 11 points
        self.assertEqual(guard.classify(OK, past), "big")

    def test_add_or_remove_is_big(self):
        added = {**OK, "VNQ": 0.05, "VEA": 0.05}
        self.assertEqual(guard.classify(OK, added), "big")
        removed = {k: v for k, v in added.items() if k != "VTI"}
        self.assertEqual(guard.classify(added, removed), "big")

    def test_shift_is_total_variation(self):
        self.assertAlmostEqual(guard.shift(OK, {**OK, "VTI": 0.30, "VXUS": 0.25}), 0.05)

    def test_one_auto_change_per_week(self):
        now = datetime(2026, 3, 1)
        self.assertTrue(guard.auto_change_allowed(None, now))
        self.assertTrue(guard.auto_change_allowed(now - timedelta(days=7), now))
        self.assertFalse(guard.auto_change_allowed(now - timedelta(days=6, hours=23), now))


class Settings(unittest.TestCase):
    def check(self, **kw):
        base = dict(buy_weekday=0, multiplier=1, monthly_cap_cents=20_000)
        return guard.validate_settings(**{**base, **kw})

    def test_buy_day_is_a_weekday(self):
        for d in range(5):
            self.assertEqual(self.check(buy_weekday=d), [])
        for d in (-1, 5, 6, True, "monday"):
            self.assertTrue(self.check(buy_weekday=d), d)

    def test_multiplier(self):
        for m in (1, 2, 3):
            self.assertEqual(self.check(multiplier=m), [])
        for m in (0, 4, 1.5, -1):
            self.assertTrue(self.check(multiplier=m))

    def test_monthly_cap_minimum(self):
        self.assertEqual(self.check(monthly_cap_cents=100), [])
        self.assertTrue(self.check(monthly_cap_cents=99))

    def test_limits_can_tighten_not_loosen(self):
        self.assertEqual(guard.validate_limits(Limits(min_holdings=6, max_etf_weight=0.30)), [])
        self.assertTrue(guard.validate_limits(Limits(min_holdings=4)))
        self.assertTrue(guard.validate_limits(Limits(max_etf_weight=0.36)))
        self.assertTrue(guard.validate_limits(Limits(max_stock_weight=0.11)))
        self.assertTrue(guard.validate_limits(Limits(max_stocks_total=0.31)))
        self.assertTrue(guard.validate_limits(Limits(max_etf_weight=0)))


class Buys(unittest.TestCase):
    def test_monthly_cap_at_limit_and_past(self):
        self.assertEqual(guard.validate_buy(amount_cents=5_000, invested_this_month_cents=15_000,
                                            monthly_cap_cents=20_000), [])
        self.assertTrue(guard.validate_buy(amount_cents=5_001, invested_this_month_cents=15_000,
                                           monthly_cap_cents=20_000))

    def test_cash(self):
        self.assertTrue(guard.validate_buy(amount_cents=5_000, invested_this_month_cents=0,
                                           monthly_cap_cents=20_000, cash_cents=4_999))

    def test_order_plan(self):
        plan = {"VTI": 1_750, "VXUS": 1_000, "BND": 1_000, "SGOV": 750, "VNQ": 500}
        self.assertEqual(guard.validate_order_plan(plan, OK, 5_000), [])
        self.assertTrue(guard.validate_order_plan({**plan, "VNQ": -500}, OK, 4_000))  # a sell
        self.assertTrue(guard.validate_order_plan({**plan, "VNQ": 99, "SGOV": 1_151}, OK, 5_000))
        self.assertTrue(guard.validate_order_plan({**plan, "TSLA": 100}, OK, 5_100))
        self.assertTrue(guard.validate_order_plan(plan, OK, 5_001))
        self.assertTrue(guard.validate_order_plan({}, OK, 0))
        self.assertTrue(guard.validate_order_plan({**plan, "VNQ": 500.0}, OK, 5_000))

    def test_tradable(self):
        self.assertEqual(guard.check_tradable("VTI", {"tradable": True, "fractionable": True}), [])
        self.assertTrue(guard.check_tradable("VTI", {"tradable": True, "fractionable": False}))
        self.assertTrue(guard.check_tradable("VTI", {"tradable": False, "fractionable": True}))
        self.assertTrue(guard.check_tradable("VTI", None))


if __name__ == "__main__":
    unittest.main()
