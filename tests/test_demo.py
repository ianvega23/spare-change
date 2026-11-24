import os

from spare.cli import DEMO_TARGETS, demo, main

from tests.helpers import TempDirCase


class DemoTests(TempDirCase):
    def test_buys_only_when_pot_passes_trigger(self):
        lines = []
        ledger = demo(12, 1, 50, 7, self.ledger_path, out=lines.append)
        buys = ledger.of_type("buy")
        self.assertTrue(buys, "the demo should make at least one buy")
        running = 0
        events = ledger.events
        for i, e in enumerate(events):
            if e["type"] == "roundup":
                running += e["cents"]
            elif e["type"] == "buy" and (i == 0 or events[i - 1]["type"] != "buy"):
                self.assertGreaterEqual(running, 5_000)  # the pot had reached $50
                batch = sum(b["notional_cents"] for b in events[i:] if b.get("batch_id") == e["batch_id"])
                running -= batch
        self.assertEqual({b["symbol"] for b in buys}, set(DEMO_TARGETS))
        self.assertTrue(any("BUY" in line for line in lines))
        self.assertGreaterEqual(ledger.pot_cents(), 0)
        self.assertLess(ledger.pot_cents(), 5_000)

    def test_cli_entry_point(self):
        self.assertEqual(main(["demo", "--weeks", "2", "--ledger", os.path.join(self.dir, "l.jsonl")]), 0)
        self.assertEqual(main(["brain"]), 2)
