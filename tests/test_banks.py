"""Teller and Plaid adapters against fixtures and a scripted transport. No network."""
import base64
import json
import os
import pathlib
import stat
import tempfile
import threading
import unittest
import urllib.request

from spare import bank_tools, enroll
from spare.core import invest
from spare.core.bank import BankError, RelinkNeeded
from spare.core.bank_plaid import PlaidBank
from spare.core.bank_teller import TellerBank, to_spent_cents
from spare.core.config import ConfigError, from_dict
from spare.core.http import NetworkError, Response
from spare.fake import FakeBroker
from tests.helpers import UNIVERSE, TempDirCase

FIX = pathlib.Path(__file__).parent / "fixtures"
ACCOUNTS = json.loads((FIX / "teller_accounts.json").read_text())
TXNS = json.loads((FIX / "teller_transactions.json").read_text())
TOKEN = "token_supersecret123"


class Script:
    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        path = url.split(".io", 1)[-1].split(".com", 1)[-1].split("?", 1)[0]
        queue = self.routes.get(path)
        if not queue:
            return Response(404, b'{"error":{"code":"not_found","message":"nope"}}', {})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return Response(item[0], json.dumps(item[1]).encode(), {})


def teller(routes, **kw):
    t = Script(routes)
    return TellerBank(TOKEN, transport=t, sleep=lambda s: None, **kw), t


class TellerTests(unittest.TestCase):
    def test_parses_posted_purchases_pending_and_credits(self):
        bank, t = teller({"/accounts": [(200, ACCOUNTS)],
                          "/accounts/acc_checking1/transactions": [(200, TXNS)]})
        txns = {x.id: x for x in bank.transactions("2026-02-20", "2026-03-03")}
        self.assertEqual(txns["txn_001"].amount_cents, 425)
        self.assertTrue(txns["txn_001"].posted)
        self.assertEqual(txns["txn_002"].amount_cents, 1001)
        self.assertEqual(txns["txn_004"].amount_cents, -185_000)  # payroll: money in
        self.assertEqual(txns["txn_005"].amount_cents, -2399)     # refund: money in
        self.assertFalse(txns["txn_006"].posted)                  # pending
        self.assertLessEqual(txns["txn_007"].amount_cents, 0)     # transfer never rounds up

    def test_positive_sign_setting(self):
        bank, _ = teller({"/accounts/acc_x/transactions": [(200, [dict(TXNS[0], amount="4.25")])]},
                         account_id="acc_x", spending_sign="positive")
        self.assertEqual(bank.transactions("2026-03-01", "2026-03-03")[0].amount_cents, 425)

    def test_auth_and_query(self):
        bank, t = teller({"/accounts/acc_x/transactions": [(200, [])]}, account_id="acc_x")
        bank.transactions("2026-02-20", "2026-03-03")
        method, url, headers, _ = t.calls[0]
        self.assertEqual(headers["Authorization"], "Basic " + base64.b64encode(f"{TOKEN}:".encode()).decode())
        self.assertIn("start_date=2026-02-20", url)
        self.assertIn("end_date=2026-03-03", url)
        self.assertNotIn(TOKEN, repr(bank))

    def test_picks_the_one_open_checking_account(self):
        bank, _ = teller({"/accounts": [(200, ACCOUNTS)]})
        self.assertEqual(bank.checking_account_id(), "acc_checking1")

    def test_ambiguous_checking_accounts(self):
        two = ACCOUNTS + [dict(ACCOUNTS[0], id="acc_checking2")]
        bank, _ = teller({"/accounts": [(200, two)]})
        with self.assertRaisesRegex(BankError, "2 open checking"):
            bank.checking_account_id()

    def test_pagination_with_from_id(self):
        import spare.core.bank_teller as bt
        old = bt.PAGE_SIZE
        bt.PAGE_SIZE = 3
        try:
            bank, t = teller({"/accounts/acc_x/transactions": [(200, TXNS[:3]), (200, TXNS[3:5])]},
                             account_id="acc_x")
            self.assertEqual(len(bank.transactions("2026-02-20", "2026-03-03")), 5)
            self.assertIn("from_id=txn_003", t.calls[1][1])
        finally:
            bt.PAGE_SIZE = old

    def test_disconnected_enrollment_needs_relink(self):
        err = {"error": {"code": "enrollment.disconnected.user_action.mfa_required", "message": "User MFA is required."}}
        bank, _ = teller({"/accounts/acc_x/transactions": [(404, err)]}, account_id="acc_x")
        with self.assertRaisesRegex(RelinkNeeded, "mfa_required"):
            bank.transactions("2026-02-20", "2026-03-03")

    def test_revoked_token_needs_relink(self):
        bank, _ = teller({"/accounts/acc_x/transactions": [(403, {"error": {"code": "forbidden"}})]}, account_id="acc_x")
        with self.assertRaises(RelinkNeeded):
            bank.transactions("2026-02-20", "2026-03-03")

    def test_retries_rate_limits_and_bank_outages(self):
        bank, t = teller({"/accounts/acc_x/transactions": [(429, {}), NetworkError("timeout"), (502, {}), (200, TXNS)]},
                         account_id="acc_x")
        self.assertEqual(len(bank.transactions("2026-02-20", "2026-03-03")), len(TXNS))
        self.assertEqual(len(t.calls), 4)

    def test_needs_certificate_outside_sandbox(self):
        with self.assertRaises(ValueError):
            TellerBank(TOKEN, environment="production")
        TellerBank(TOKEN, environment="sandbox")  # sandbox works without one

    def test_amount_parsing(self):
        self.assertEqual(to_spent_cents("-4.25", "negative"), 425)
        self.assertEqual(to_spent_cents("4.25", "negative"), -425)
        for bad in ("abc", "-4.255"):
            with self.assertRaises(BankError):
                to_spent_cents(bad, "negative")

    def test_bank_check_output(self):
        bank, _ = teller({"/accounts": [(200, ACCOUNTS)],
                          "/accounts/acc_checking1/transactions": [(200, TXNS)]})
        from datetime import date
        lines = []
        bank_tools.bank_check(bank, today=date(2026, 3, 3), out=lines.append)
        text = "\n".join(lines)
        self.assertIn("ending 1234", text)
        self.assertIn("<- used", text)
        self.assertIn("+$0.75", text)
        self.assertNotIn(TOKEN, text)


class RelinkInInvestTests(TempDirCase):
    def test_relink_alert_keeps_the_pot(self):
        class Broken:
            name = "broken"

            def transactions(self, since, until):
                raise RelinkNeeded("your bank connection needs attention")

        r = invest.run(from_dict({}), self.ledger(), Broken(), FakeBroker(),
                       {"VTI": 0.35, "VXUS": 0.20, "BND": 0.20, "SGOV": 0.15, "VNQ": 0.10},
                       UNIVERSE, "2026-03-02")
        self.assertTrue(any("needs attention" in e for e in r.errors))
        self.assertEqual([e.get("kind") for e in self.ledger().of_type("alert")], ["relink"])


class PlaidTests(unittest.TestCase):
    def page(self, txns, total):
        return (200, {"transactions": txns, "total_transactions": total})

    def test_parses_plaid_convention(self):
        txns = [{"transaction_id": "p1", "date": "2026-03-01", "name": "Coffee", "amount": 4.25, "pending": False},
                {"transaction_id": "p2", "date": "2026-03-01", "name": "Paycheck", "amount": -1850.0, "pending": False,
                 "personal_finance_category": {"primary": "INCOME"}},
                {"transaction_id": "p3", "date": "2026-03-02", "name": "To savings", "amount": 200.0, "pending": False,
                 "personal_finance_category": {"primary": "TRANSFER_OUT"}},
                {"transaction_id": "p4", "date": "2026-03-02", "name": "Bakery", "amount": 3.1, "pending": True}]
        t = Script({"/transactions/get": [self.page(txns, 4)]})
        bank = PlaidBank("cid", "sec", "access-x", transport=t, sleep=lambda s: None)
        out = {x.id: x for x in bank.transactions("2026-02-20", "2026-03-03")}
        self.assertEqual(out["p1"].amount_cents, 425)
        self.assertEqual(out["p2"].amount_cents, -185_000)
        self.assertLessEqual(out["p3"].amount_cents, 0)
        self.assertFalse(out["p4"].posted)
        self.assertEqual(out["p4"].amount_cents, 310)  # exact, no float drift

    def test_pages(self):
        one = {"transaction_id": "a", "date": "2026-03-01", "name": "x", "amount": 1.5, "pending": False}
        t = Script({"/transactions/get": [self.page([one], 2), self.page([dict(one, transaction_id="b")], 2)]})
        bank = PlaidBank("cid", "sec", "access-x", transport=t, sleep=lambda s: None)
        self.assertEqual([x.id for x in bank.transactions("2026-02-20", "2026-03-03")], ["a", "b"])
        self.assertEqual(json.loads(t.calls[1][3])["options"]["offset"], 1)

    def test_login_required_needs_relink(self):
        t = Script({"/transactions/get": [(400, {"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "login"})]})
        bank = PlaidBank("cid", "sec", "access-x", transport=t, sleep=lambda s: None)
        with self.assertRaises(RelinkNeeded):
            bank.transactions("2026-02-20", "2026-03-03")


class ConfigBankTests(unittest.TestCase):
    def test_bank_settings(self):
        cfg = from_dict({"bank": "plaid", "bank_environment": "sandbox", "teller_spending_sign": "positive"})
        self.assertEqual((cfg.bank, cfg.bank_environment, cfg.teller_spending_sign), ("plaid", "sandbox", "positive"))
        for bad in ({"bank": "chase"}, {"bank_environment": "prod"}, {"teller_spending_sign": "up"},
                    {"bank_account_id": 5}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                from_dict(bad)


class EnrollTests(unittest.TestCase):
    def test_page_escapes_and_includes_connect(self):
        page = enroll.render_page('app_"x</script>', "sandbox", "nonce123", "enr_1")
        self.assertIn(enroll.CONNECT_JS, page)
        self.assertIn('enrollmentId: "enr_1"', page)
        self.assertNotIn('app_"x</script>"', page.replace('\\"', ""))  # JSON-escaped

    def test_save_token_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            path = enroll.save_token(pathlib.Path(d) / "teller", {"accessToken": TOKEN, "enrollmentId": "enr_1"})
            self.assertEqual(path.read_text().strip(), TOKEN)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with self.assertRaises(ValueError):
                enroll.save_token(pathlib.Path(d), {"accessToken": "has space"})

    def test_server_needs_the_nonce(self):
        import http.server
        with tempfile.TemporaryDirectory() as d:
            done = threading.Event()
            handler = enroll.make_handler("<html></html>", "right", pathlib.Path(d), done, out=lambda *_: None)
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                def post(nonce):
                    req = urllib.request.Request(base + "/token", json.dumps({"accessToken": TOKEN}).encode(),
                                                 {"Content-Type": "application/json", "X-Spare-Nonce": nonce})
                    try:
                        return opener.open(req, timeout=5).status
                    except urllib.error.HTTPError as e:
                        return e.code
                self.assertEqual(post("wrong"), 403)
                self.assertFalse(done.is_set())
                self.assertEqual(post("right"), 200)
                self.assertTrue(done.is_set())
                self.assertEqual(opener.open(base + "/", timeout=5).status, 200)
            finally:
                server.shutdown()

    def test_server_ignores_requests_for_other_hosts(self):
        """DNS rebinding: a page on evil.example pointed at 127.0.0.1 still sends Host: evil.example."""
        import http.server
        with tempfile.TemporaryDirectory() as d:
            done = threading.Event()
            handler = enroll.make_handler("<html>nonce</html>", "right", pathlib.Path(d), done, out=lambda *_: None)
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            port = server.server_address[1]
            base = f"http://127.0.0.1:{port}"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def status(req):
                try:
                    return opener.open(req, timeout=5).status
                except urllib.error.HTTPError as e:
                    return e.code
            try:
                evil = {"Host": f"evil.example:{port}"}
                self.assertEqual(status(urllib.request.Request(base + "/", headers=evil)), 403)
                req = urllib.request.Request(base + "/token", json.dumps({"accessToken": TOKEN}).encode(),
                                             {**evil, "Content-Type": "application/json", "X-Spare-Nonce": "right"})
                self.assertEqual(status(req), 403)
                self.assertFalse(done.is_set())
                local = {"Host": f"localhost:{port}"}
                self.assertEqual(status(urllib.request.Request(base + "/", headers=local)), 200)
            finally:
                server.shutdown()


if __name__ == "__main__":
    unittest.main()
