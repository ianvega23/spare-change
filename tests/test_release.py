"""Phase 10: the S&P 500 list, explanation checks with stocks on or off, and release files."""
import csv
import pathlib
import unittest

from spare.brain import allocator
from spare.core import config, guard

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Sp500Tests(unittest.TestCase):
    def test_list_loads_and_is_stocks_only(self):
        universe = config.load_universe()
        self.assertGreater(len(universe.stocks), 490)
        self.assertIn("AAPL", universe.stocks)
        self.assertFalse(universe.stocks & universe.etfs)
        with open(ROOT / "allowlist" / "sp500.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(set(rows[0]), {"symbol", "name", "sector"})

    def test_stocks_are_offered_only_when_enabled(self):
        universe = config.load_universe()
        self.assertEqual(allocator.offered(universe, guard.Limits()), universe.etfs)
        self.assertIn("AAPL", allocator.offered(universe, guard.Limits(allow_stocks=True)))


class ExplanationTests(unittest.TestCase):
    TARGETS = {"VTI": 0.35, "VXUS": 0.2, "BND": 0.2, "SGOV": 0.15, "VNQ": 0.1}

    def check(self, summary, known):
        return allocator.check_explanations(self.TARGETS, {}, summary, known, "")

    def test_words_that_are_one_letter_tickers_pass(self):
        known = config.load_universe().etfs | {"A", "C", "T"}
        self.assertEqual(self.check("A broad mix. I chose it for a long horizon.", known), [])

    def test_naming_an_offered_fund_outside_the_mix_fails(self):
        known = config.load_universe().etfs
        self.assertTrue(self.check("Like VTI, but VUG would be riskier.", known))

    def test_stock_names_only_count_when_stocks_were_offered(self):
        universe = config.load_universe()
        summary = "AI and US stocks in VTI."  # "AI" is not a stock, "US" neither
        self.assertEqual(self.check(summary, allocator.offered(universe, guard.Limits())), [])


if __name__ == "__main__":
    unittest.main()
