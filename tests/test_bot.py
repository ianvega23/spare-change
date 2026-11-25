"""Phase 5: Telegram client, signed buttons, the hourly relay, onboarding and bot.yml."""
import json
import pathlib
import re
import tempfile
import tomllib
import unittest
from datetime import datetime, timedelta, timezone

from spare import jobs
from spare.bot import onboard, relay
from spare.bot.telegram import Telegram, TelegramError, chat_of
from spare.brain import starter
from spare.core import proposals
from spare.core.http import Response
from spare.core.state import State
from spare.fake import FakeBroker
from tests.test_jobs import Bank

KEY = b"k" * 32
ME, STRANGER = 111, 999
NOW = datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)
MIX = {"VTI": 0.35, "BND": 0.25, "VXUS": 0.20, "SGOV": 0.10, "VNQ": 0.10}
ROOT = pathlib.Path(__file__).resolve().parents[1]


class FakeTelegram:
    def __init__(self, updates=()):
        self.sent, self.answers = [], []
        self.queue = list(updates)
        self.offsets = []

    def send(self, chat_id, text, buttons=None):
        self.sent.append((chat_id, text, buttons))
        return {}

    def updates(self, offset, timeout=0):
        self.offsets.append(offset)
        out = [u for u in self.queue if offset is None or u["update_id"] >= offset]
        self.queue = []
        return out

    def answer(self, cid, text):
        self.answers.append(text)


def msg(uid, text, chat=ME):
    return {"update_id": uid, "message": {"chat": {"id": chat}, "text": text}}


def tap(uid, data, chat=ME):
    return {"update_id": uid, "callback_query": {"id": f"cb{uid}", "data": data,
                                                 "message": {"chat": {"id": chat}}}}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.state = State(self.dir).ensure()

    def tearDown(self):
        self._tmp.cleanup()

    def relay(self, tg, now=NOW, status_text=None):
        return relay.run_once(self.dir, tg, ME, KEY, now=now, status_text=status_text)

    def proposal(self, targets=MIX, now=NOW):
        return proposals.create(self.state, targets, summary="s", reasons={"VTI": "core"},
                                source="test", now=now)


class ButtonTests(unittest.TestCase):
    def test_roundtrip_and_forgery(self):
        data = proposals.button_data(KEY, "ab12cd34", "a")
        self.assertLessEqual(len(data.encode()), 64)
        self.assertEqual(proposals.parse_button(KEY, data), ("ab12cd34", "approve"))
        self.assertIsNone(proposals.parse_button(b"x" * 32, data))       # other key
        self.assertIsNone(proposals.parse_button(KEY, data.replace("a:", "s:", 1)))  # action swapped
        self.assertIsNone(proposals.parse_button(KEY, "a:ab12cd34:" + "0" * 20))
        self.assertIsNone(proposals.parse_button(KEY, "garbage"))


class RelayTests(Base):
    def test_sends_outbox_and_deletes_it(self):
        self.state.queue_message("buy", "Bought $12.40 across 5 funds")
        tg = FakeTelegram()
        self.assertEqual(self.relay(tg).sent, 1)
        self.assertEqual(tg.sent[0][1], "Bought $12.40 across 5 funds")
        self.assertEqual(self.state.outbox(), [])

    def test_offers_a_proposal_once_with_signed_buttons(self):
        p = self.proposal()
        tg = FakeTelegram()
        self.relay(tg)
        self.relay(tg)
        offers = [s for s in tg.sent if s[2]]
        self.assertEqual(len(offers), 1)
        self.assertIn("VTI 35%: core", offers[0][1])
        self.assertEqual(proposals.parse_button(KEY, offers[0][2][0][1]), (p["id"], "approve"))

    def test_approve_writes_targets(self):
        p = self.proposal()
        r = self.relay(FakeTelegram([tap(1, proposals.button_data(KEY, p["id"], "a"))]))
        self.assertEqual(r.decisions, ["approve"])
        self.assertEqual(self.state.targets(), MIX)
        self.assertEqual(proposals.load(self.state, p["id"])["status"], "approved")
        log = (pathlib.Path(self.dir) / "decisions.jsonl").read_text()
        self.assertIn('"status": "approved"', log)

    def test_skip_keeps_targets(self):
        p = self.proposal()
        self.relay(FakeTelegram([tap(1, proposals.button_data(KEY, p["id"], "s"))]))
        self.assertEqual(self.state.targets(), {})
        self.assertEqual(proposals.load(self.state, p["id"])["status"], "skipped")

    def test_forged_tap_changes_nothing(self):
        p = self.proposal()
        tg = FakeTelegram([tap(1, f"a:{p['id']}:" + "f" * 20)])
        self.assertEqual(self.relay(tg).decisions, ["forged"])
        self.assertEqual(self.state.targets(), {})
        self.assertEqual(proposals.load(self.state, p["id"])["status"], "pending")

    def test_other_chats_are_ignored(self):
        p = self.proposal()
        tg = FakeTelegram([tap(1, proposals.button_data(KEY, p["id"], "a"), chat=STRANGER),
                           msg(2, "/pause", chat=STRANGER)])
        r = self.relay(tg)
        self.assertEqual(r.ignored_other_chats, 2)
        self.assertEqual(self.state.targets(), {})
        self.assertFalse(self.state.paused())
        self.assertTrue(all(chat == ME for chat, _, _ in tg.sent))

    def test_expiry_means_skip(self):
        p = self.proposal(now=NOW - timedelta(days=7, minutes=1))
        tg = FakeTelegram()
        self.relay(tg)
        self.assertEqual(proposals.load(self.state, p["id"])["status"], "expired")
        self.assertIn("expired", tg.sent[0][1])

    def test_tap_after_expiry(self):
        p = self.proposal(now=NOW - timedelta(days=6))
        data = proposals.button_data(KEY, p["id"], "a")
        reply = proposals.decide(self.state, p["id"], "approve", jobs.config_mod.load_universe(),
                                 jobs.config_mod.from_dict({}).limits, now=NOW + timedelta(days=2))
        self.assertIn("expired", reply)
        self.assertEqual(self.state.targets(), {})
        self.assertTrue(data)

    def test_guard_rechecks_on_approve(self):
        p = self.proposal(targets={"VTI": 1.0})
        self.relay(FakeTelegram([tap(1, proposals.button_data(KEY, p["id"], "a"))]))
        self.assertEqual(self.state.targets(), {})
        self.assertEqual(proposals.load(self.state, p["id"])["status"], "rejected")

    def test_double_tap(self):
        p = self.proposal()
        data = proposals.button_data(KEY, p["id"], "a")
        self.relay(FakeTelegram([tap(1, data)]))
        tg = FakeTelegram([tap(2, proposals.button_data(KEY, p["id"], "s"))])
        self.relay(tg)
        self.assertEqual(self.state.targets(), MIX)
        self.assertIn("Already approved", tg.answers[0])

    def test_pause_resume_and_status(self):
        tg = FakeTelegram([msg(1, "/pause"), msg(2, "/status"), msg(3, "hello")])
        self.relay(tg, status_text=lambda: "Pot: $1.00")
        self.assertTrue(self.state.paused())
        texts = [t for _, t, _ in tg.sent]
        self.assertIn("Pot: $1.00", texts)
        self.assertTrue(any("Commands:" in t for t in texts))
        # the money job honours the pause
        self.assertEqual(jobs.run_invest(self.dir, "2026-03-02", bank=Bank(fail=AssertionError("no")),
                                         broker=FakeBroker(), out=lambda *_: None), 0)
        self.relay(FakeTelegram([msg(4, "/resume")]))
        self.assertFalse(self.state.paused())

    def test_offset_is_saved(self):
        self.relay(FakeTelegram([msg(41, "/status")]))
        tg = FakeTelegram()
        self.relay(tg)
        self.assertEqual(tg.offsets, [42])


class OnboardTests(Base):
    def scripted(self, answers):
        answers = list(answers)
        asked = []

        def ask(question, options):
            asked.append((question, options))
            return answers.pop(0)
        return ask, asked

    def test_conversation_saves_profile_config_and_a_proposal(self):
        ask, asked = self.scripted(["", 'A house deposit, then "retirement"', "Big drops are fine",
                                    "Not for 15 years", "2", "friday", "300", "no"])
        p = onboard.run(self.dir, ask, starter.propose, out=lambda *_: None)
        self.assertEqual(asked[0][0], asked[1][0])  # empty answer asked again
        profile = tomllib.loads((pathlib.Path(self.dir) / "profile.toml").read_text())
        self.assertEqual(profile["goals"], 'A house deposit, then "retirement"')
        cfg = jobs.load_config(self.state)
        self.assertEqual((cfg.multiplier, cfg.buy_weekday, cfg.monthly_cap_cents, cfg.limits.allow_stocks),
                         (2, 4, 30_000, False))
        self.assertTrue(cfg.dry_run)  # onboarding never switches off dry run
        self.assertEqual(p["source"], "starter:growth")
        self.assertEqual(proposals.pending(self.state)[0]["id"], p["id"])

    def test_keeps_existing_config(self):
        (pathlib.Path(self.dir) / "config.toml").write_text('dry_run = false\nbank = "plaid"\n')
        ask, _ = self.scripted(["g", "r", "1 year", "1", "monday", "100", "no"])
        onboard.run(self.dir, ask, starter.propose, out=lambda *_: None)
        cfg = jobs.load_config(self.state)
        self.assertEqual((cfg.dry_run, cfg.bank), (False, "plaid"))

    def test_starter_mixes_pass_the_guard(self):
        from spare.core import config, guard
        for name, mix in starter.MIXES.items():
            self.assertEqual(guard.validate_targets(mix, config.load_universe()), [], name)
        self.assertEqual(starter.pick_mix({"horizon": "in 2 years"}), "conservative")
        self.assertEqual(starter.pick_mix({"horizon": "not sure"}), "balanced")

    def test_telegram_ask_only_listens_to_my_chat(self):
        tg = FakeTelegram()
        asker = onboard.TelegramAsk(tg, ME, poll_seconds=0)
        tg.queue = [msg(5, "ignore me", chat=STRANGER), msg(6, "my answer")]
        self.assertEqual(asker("Question?", None), "my answer")
        tg.queue = [msg(7, "typed instead"), tap(8, "o:bogus"), tap(9, "o:2")]
        self.assertEqual(asker("Multiplier?", [("1x", "1"), ("2x", "2")]), "2")


class TelegramClientTests(unittest.TestCase):
    TOKEN = "123456:SECRET-token"

    def test_send_and_errors_hide_the_token(self):
        calls = []

        def transport(method, url, headers, body):
            calls.append((url, json.loads(body)))
            if "getUpdates" in url:
                return Response(401, b'{"ok": false, "description": "Unauthorized"}', {})
            return Response(200, b'{"ok": true, "result": {"message_id": 1}}', {})
        t = Telegram(self.TOKEN, transport=transport)
        t.send(ME, "hi", buttons=[("Approve", "a:x:y")])
        self.assertEqual(calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "a:x:y")
        with self.assertRaises(TelegramError) as ctx:
            t.updates(None)
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertNotIn("SECRET", repr(t))
        with self.assertRaises(ValueError):
            Telegram("nocolon")

    def test_chat_of(self):
        self.assertEqual(chat_of(msg(1, "x")), ME)
        self.assertEqual(chat_of(tap(1, "x", chat=STRANGER)), STRANGER)
        self.assertIsNone(chat_of({"update_id": 1}))


class BotWorkflowTests(unittest.TestCase):
    def test_hardening(self):
        wf = (ROOT / ".github" / "workflows" / "bot.yml").read_text()
        for u in re.findall(r"uses:\s*(\S+)", wf):
            self.assertRegex(u, r"^actions/(checkout|setup-python)@[0-9a-f]{40}$")
        self.assertIn("environment: telegram", wf)
        self.assertIn("if: vars.SPARE_ENABLED == 'true'", wf)
        for money_secret in ("APCA", "TELLER", "PLAID", "ANTHROPIC"):
            self.assertNotIn(money_secret, wf)


if __name__ == "__main__":
    unittest.main()
