"""`spare enroll --bank plaid` against a fake Plaid. No network."""
import json
import pathlib
import tempfile
import threading
import unittest
import urllib.request

from spare import enroll, enroll_plaid
from spare.core.http import Response

CLIENT, SECRET = "client-123", "secret-do-not-leak"
ACCESS = "access-production-abc"


class FakePlaid:
    def __init__(self, link_status=200, exchange_status=200):
        self.calls = []
        self.link_status, self.exchange_status = link_status, exchange_status

    def __call__(self, method, url, headers, body):
        data = json.loads(body)
        self.calls.append((url, data))
        if url.endswith("/link/token/create"):
            if self.link_status != 200:
                return Response(400, json.dumps({"error_code": "INVALID_API_KEYS",
                                                 "error_message": "invalid client_id or secret provided"}).encode(), {})
            return Response(200, b'{"link_token": "link-production-xyz"}', {})
        if url.endswith("/item/public_token/exchange"):
            if self.exchange_status != 200:
                return Response(400, b'{"error_code": "INVALID_PUBLIC_TOKEN", "error_message": "bad"}', {})
            return Response(200, json.dumps({"access_token": ACCESS, "item_id": "item_1"}).encode(), {})
        raise AssertionError(url)


class LinkTokenTests(unittest.TestCase):
    def test_asks_for_transactions_only_on_production(self):
        t = FakePlaid()
        self.assertEqual(enroll_plaid.link_token(CLIENT, SECRET, transport=t), "link-production-xyz")
        url, body = t.calls[0]
        self.assertTrue(url.startswith("https://production.plaid.com/"))
        self.assertEqual(body["products"], ["transactions"])  # never payments or transfer
        self.assertEqual(body["country_codes"], ["US"])

    def test_relink_uses_update_mode(self):
        t = FakePlaid()
        enroll_plaid.link_token(CLIENT, SECRET, access_token=ACCESS, environment="sandbox", transport=t)
        url, body = t.calls[0]
        self.assertTrue(url.startswith("https://sandbox.plaid.com/"))
        self.assertEqual(body["access_token"], ACCESS)
        self.assertNotIn("products", body)

    def test_bad_keys_explained_without_the_secret(self):
        out = []
        code = enroll_plaid.run(CLIENT, SECRET, transport=FakePlaid(link_status=400), out=out.append,
                                open_browser=False)
        self.assertEqual(code, 1)
        self.assertIn("INVALID_API_KEYS", out[0])
        self.assertNotIn(SECRET, out[0])

    def test_page_escapes_the_token(self):
        page = enroll_plaid.render_page('link-"</script>', "n")
        self.assertIn(enroll_plaid.LINK_JS, page)
        self.assertNotIn('link-"</script>"', page.replace('\\"', ""))


class SaveTests(unittest.TestCase):
    def test_exchanges_and_saves_privately(self):
        with tempfile.TemporaryDirectory() as d:
            folder = pathlib.Path(d) / "plaid"
            t = FakePlaid()
            save = enroll_plaid.make_saver(CLIENT, SECRET, "production", t)
            path = save(folder, {"publicToken": "public-production-1", "institution": "Chase",
                                 "accounts": [{"id": "acc_1", "name": "Checking", "mask": "1234",
                                               "subtype": "checking", "extra": "dropped"}]})
            self.assertEqual(path.read_text(encoding="utf-8").strip(), ACCESS)
            meta = json.loads((folder / "item.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["item_id"], "item_1")
            self.assertEqual(meta["accounts"][0], {"id": "acc_1", "name": "Checking", "mask": "1234",
                                                   "subtype": "checking"})
            self.assertEqual(t.calls[0][1]["public_token"], "public-production-1")

    def test_rejects_junk_and_failed_exchanges(self):
        with tempfile.TemporaryDirectory() as d:
            save = enroll_plaid.make_saver(CLIENT, SECRET, "production", FakePlaid())
            with self.assertRaises(ValueError):
                save(pathlib.Path(d), {"publicToken": "access-sneaky"})
            bad = enroll_plaid.make_saver(CLIENT, SECRET, "production", FakePlaid(exchange_status=400))
            with self.assertRaisesRegex(ValueError, "INVALID_PUBLIC_TOKEN"):
                bad(pathlib.Path(d), {"publicToken": "public-production-1"})
            self.assertFalse((pathlib.Path(d) / "token.txt").exists())

    def test_relink_keeps_the_same_token(self):
        with tempfile.TemporaryDirectory() as d:
            t = FakePlaid()
            save = enroll_plaid.make_saver(CLIENT, SECRET, "production", t, existing_token=ACCESS)
            path = save(pathlib.Path(d), {"publicToken": "public-production-2"})
            self.assertEqual(path.read_text(encoding="utf-8").strip(), ACCESS)
            self.assertEqual(t.calls, [])  # update mode needs no exchange

    def test_server_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            done = threading.Event()
            save = enroll_plaid.make_saver(CLIENT, SECRET, "production", FakePlaid())
            handler = enroll.make_handler("<html></html>", "right", pathlib.Path(d), done,
                                          out=lambda *_: None, save=save, label="Plaid")
            import http.server
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                port = server.server_address[1]
                req = urllib.request.Request(f"http://127.0.0.1:{port}/token", method="POST",
                                             data=json.dumps({"publicToken": "public-production-1"}).encode(),
                                             headers={"X-Spare-Nonce": "right", "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    self.assertEqual(r.status, 200)
                self.assertTrue(done.is_set())
                self.assertEqual((pathlib.Path(d) / "token.txt").read_text(encoding="utf-8").strip(), ACCESS)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
