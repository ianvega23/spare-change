from spare.core.ledger import Ledger, LedgerError

from tests.helpers import TempDirCase


class LedgerTests(TempDirCase):
    def test_append_only_and_reload(self):
        led = self.ledger()
        led.append("roundup", txn_id="a", date="2026-01-05", cents=75, amount_cents=425)
        led.append("roundup", txn_id="b", date="2026-01-06", cents=50, amount_cents=150)
        with open(self.ledger_path) as f:
            first_line = f.readline()
        led.append("buy", date="2026-01-07", batch_id="x", symbol="VTI", notional_cents=100,
                   client_order_id="spare-1", status="filled", qty="0.01", price="100.00")
        with open(self.ledger_path) as f:
            self.assertEqual(f.readline(), first_line)  # earlier lines untouched
        again = Ledger(self.ledger_path)
        self.assertEqual(len(again.events), 3)
        self.assertEqual(again.pot_cents(), 25)
        self.assertEqual(again.seen_txn_ids(), {"a", "b"})
        self.assertEqual(again.holdings_cents(), {"VTI": 100})
        self.assertEqual(again.invested_in_month_cents("2026-01"), 100)
        self.assertEqual(again.invested_in_month_cents("2026-02"), 0)

    def test_unbatched_roundups(self):
        led = self.ledger()
        led.append("roundup", txn_id="a", date="2026-01-05", cents=75, amount_cents=425)
        led.append("buy", date="2026-01-07", batch_id="x", symbol="VTI", notional_cents=75,
                   client_order_id="spare-1", status="filled", qty="0", price="1.00")
        led.append("roundup", txn_id="b", date="2026-01-08", cents=50, amount_cents=150)
        self.assertEqual(led.unbatched_roundup_ids(), ["b"])
        self.assertEqual(led.last_batch_id(), "x")

    def test_rejects_unknown_events(self):
        with self.assertRaises(LedgerError):
            self.ledger().append("withdraw", cents=100)

    def test_rejects_corrupt_file(self):
        with open(self.ledger_path, "w") as f:
            f.write('{"type": "roundup"\n')
        with self.assertRaises(LedgerError):
            self.ledger()
