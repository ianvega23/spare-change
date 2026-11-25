import os

from spare.cli import DEMO_TARGETS, demo, main

from tests.helpers import TempDirCase


class DemoTests(TempDirCase):
    def test_buys_once_a_week_on_the_buy_day(self):
        from datetime import date
        lines = []
        ledger = demo(12, 1, "monday", 7, self.ledger_path, out=lines.append)
        buy_days = sorted({b["date"] for b in ledger.of_type("buy")})
        self.assertEqual(len(buy_days), 12)  # one buy every week
        self.assertTrue(all(date.fromisoformat(d).weekday() == 0 for d in buy_days))
        weeks = [date.fromisoformat(d).isocalendar()[:2] for d in buy_days]
        self.assertEqual(len(weeks), len(set(weeks)))
        self.assertEqual({b["symbol"] for b in ledger.of_type("buy")}, set(DEMO_TARGETS))
        self.assertTrue(any("BUY" in line for line in lines))
        roundups = sum(e["cents"] for e in ledger.of_type("roundup"))
        invested = sum(e["notional_cents"] for e in ledger.of_type("buy"))
        self.assertEqual(roundups - invested, ledger.pot_cents())

    def test_cli_entry_point(self):
        self.assertEqual(main(["demo", "--weeks", "2", "--ledger", os.path.join(self.dir, "l.jsonl")]), 0)
        self.assertEqual(main(["doctor", "--go-live", "--state", self.dir]), 2)
