"""Phase 4: state branch files, the invest/status/doctor commands and the invest workflow."""
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import date

from spare import jobs
from spare.core.bank import BankError
from spare.core.engine import Transaction
from spare.core.state import State, StateError
from spare.fake import FakeBroker

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "invest.yml").read_text()
TARGETS = {"VTI": 0.35, "VXUS": 0.20, "BND": 0.20, "SGOV": 0.15, "VNQ": 0.10}
MONDAY = "2026-03-02"


class Bank:
    name = "bank"

    def __init__(self, n=8, fail=None):
        self.n, self.fail = n, fail

    def transactions(self, since, until):
        if self.fail:
            raise self.fail
        return [Transaction(f"t{i}", "2026-03-01", "shop", 101) for i in range(self.n)]


class StateCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.state = State(self.dir).ensure()
        self.lines = []

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text):
        pathlib.Path(self.dir, name).write_text(text)

    def run_invest(self, **kw):
        kw.setdefault("bank", Bank())
        kw.setdefault("broker", FakeBroker())
        return jobs.run_invest(self.dir, MONDAY, out=self.lines.append, **kw)


class StateTests(StateCase):
    def test_targets(self):
        self.assertEqual(self.state.targets(), {})
        self.write("targets.json", json.dumps({"targets": TARGETS, "approved_at": "2026-03-01"}))
        self.assertEqual(self.state.targets(), TARGETS)
        self.write("targets.json", json.dumps(TARGETS))
        self.assertEqual(self.state.targets(), TARGETS)
        for bad in ("[1, 2]", "{nope", '{"targets": [1]}'):
            self.write("targets.json", bad)
            with self.assertRaises(StateError):
                self.state.targets()

    def test_pause_flag(self):
        self.assertFalse(self.state.paused())
        self.write("flags.json", '{"paused": true}')
        self.assertTrue(self.state.paused())
        self.write("flags.json", '["garbage"]')
        self.assertTrue(self.state.paused())  # unreadable flags fail safe

    def test_outbox(self):
        self.state.queue_message("buy", "x" * 5_000)
        self.state.queue_message("alert", "hello")
        msgs = self.state.outbox()
        self.assertEqual([m["kind"] for m in msgs], ["buy", "alert"])
        self.assertEqual(len(msgs[0]["text"]), 1_000)


class InvestJobTests(StateCase):
    def test_defaults_are_dry_run_and_record_only_the_ledger(self):
        self.write("targets.json", json.dumps(TARGETS))
        broker = FakeBroker()
        self.assertEqual(self.run_invest(broker=broker), 0)
        self.assertEqual(broker.orders, {})
        self.assertTrue(any("would buy VTI" in line for line in self.lines))
        self.assertEqual(self.state.ledger().pot_cents(), 792)
        self.assertEqual(self.state.last_run()["status"], "dry_run")

    def test_placing_orders_queues_a_message(self):
        self.write("config.toml", "dry_run = false\n")
        self.write("targets.json", json.dumps(TARGETS))
        broker = FakeBroker()
        self.assertEqual(self.run_invest(broker=broker, bank=Bank(n=20)), 0)
        self.assertEqual(len(broker.orders), 5)
        self.assertTrue(self.state.outbox()[-1]["text"].startswith("Bought $19.80 across 5 funds"))

    def test_no_targets_keeps_collecting(self):
        self.assertEqual(self.run_invest(), 0)
        self.assertEqual(self.state.last_run()["status"], "no_targets")
        self.assertEqual(self.state.ledger().pot_cents(), 792)

    def test_kill_switches(self):
        self.write("flags.json", '{"paused": true}')
        self.assertEqual(self.run_invest(bank=Bank(fail=AssertionError("must not fetch"))), 0)
        self.write("flags.json", '{"paused": false}')
        self.write("config.toml", "enabled = false\n")
        self.assertEqual(self.run_invest(bank=Bank(fail=AssertionError("must not fetch"))), 0)
        self.assertEqual(self.state.ledger().events, [])

    def test_forced_failure_exits_non_zero_and_alerts(self):
        self.assertEqual(self.run_invest(bank=Bank(fail=BankError("Teller is down"))), 1)
        self.assertFalse(self.state.last_run()["ok"])
        self.assertIn("Teller is down", self.state.outbox()[-1]["text"])

    def test_invalid_config_exits_non_zero(self):
        self.write("config.toml", "multiplier = 9\n")
        self.assertEqual(self.run_invest(), 1)

    def test_guardrail_block_exits_non_zero(self):
        self.write("config.toml", "dry_run = false\n")
        self.write("targets.json", json.dumps({"VTI": 1.0}))
        broker = FakeBroker()
        self.assertEqual(self.run_invest(broker=broker), 1)
        self.assertEqual(broker.orders, {})

    def test_live_needs_go_live_file(self):
        self.write("config.toml", 'alpaca_env = "live"\ndry_run = false\n')
        cfg = jobs.load_config(self.state)
        self.assertFalse(jobs.live_unlocked(cfg, self.state))
        with self.assertRaises(SystemExit):
            jobs.broker_from_env(cfg, self.state)
        self.write("go_live.json", '{"passed": true}')
        self.assertTrue(jobs.live_unlocked(cfg, self.state))
        self.write("config.toml", 'alpaca_env = "live"\n')  # dry run still on
        self.assertFalse(jobs.live_unlocked(jobs.load_config(self.state), self.state))


class StatusDoctorTests(StateCase):
    def test_status(self):
        self.write("targets.json", json.dumps(TARGETS))
        self.run_invest()
        lines = []
        jobs.status(self.dir, out=lines.append)
        text = "\n".join(lines)
        self.assertIn("Pot: $7.92", text)
        self.assertIn("VTI 35%", text)
        self.assertIn("dry_run", text)

    def test_doctor(self):
        env = {"TELLER_ACCESS_TOKEN": "t", "TELLER_CERT": "c", "TELLER_KEY": "k",
               "APCA_API_KEY_ID": "a", "APCA_API_SECRET_KEY": "b"}
        self.write("targets.json", json.dumps(TARGETS))
        self.state.record_run("dry_run", "2026-03-02", ok=True)
        lines = []
        self.assertEqual(jobs.doctor(self.dir, date(2026, 3, 3), env, out=lines.append), 0)
        lines.clear()
        self.assertEqual(jobs.doctor(self.dir, date(2026, 3, 10), {}, out=lines.append), 1)
        text = "\n".join(lines)
        self.assertIn("APCA_API_KEY_ID", text)
        self.assertIn("8 days ago", text)
        self.write("targets.json", json.dumps({"VTI": 1.0}))
        self.assertEqual(jobs.doctor(self.dir, date(2026, 3, 3), env, out=lambda *_: None), 1)


class WorkflowTests(unittest.TestCase):
    def test_hardening(self):
        uses = re.findall(r"uses:\s*(\S+)", WORKFLOW)
        self.assertTrue(uses)
        for u in uses:
            self.assertRegex(u, r"^actions/(checkout|setup-python)@[0-9a-f]{40}$")
        self.assertIn("environment: money", WORKFLOW)
        self.assertIn("if: vars.SPARE_ENABLED == 'true'", WORKFLOW)
        self.assertIn("group: invest", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertNotIn("ANTHROPIC", WORKFLOW)   # the AI key never reaches the money job
        self.assertNotIn("TELEGRAM", WORKFLOW)
        self.assertNotIn("pull_request", WORKFLOW)

    def step(self, name):
        m = re.search(r"- name: " + re.escape(name) + r"\n(?:.*\n)*?\s+run: \|\n((?:\s{10}.*\n|\n)+)", WORKFLOW)
        self.assertIsNotNone(m, name)
        return "\n".join(line[10:] for line in m.group(1).splitlines())

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_state_branch_steps_commit_only_state_files(self):
        checkout, commit = self.step("Check out the state branch"), self.step("Commit the ledger to the state branch")
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull}
            def sh(script, cwd):
                r = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=cwd, env=env,
                                   capture_output=True, text=True)
                if r.returncode:
                    self.fail(f"{script!r} failed: {r.stderr}")
                return r
            remote, work = os.path.join(d, "remote.git"), os.path.join(d, "work")
            sh(f"git init -q --bare {remote} && git init -q -b main {work}", d)
            sh(f"echo code > app.py && git add . && git commit -qm init && git remote add origin {remote} "
               "&& git push -q origin main", work)
            for run in (1, 2):  # first run creates the orphan branch, second appends to it
                sh(checkout, work)
                pathlib.Path(work, "state", "ledger.jsonl").open("a").write(f'{{"run": {run}}}\n')
                pathlib.Path(work, "state", "stray.txt").write_text("not committed")
                sh(commit, work)
                sh("git worktree remove --force state && git branch -D state", work)
            files = sh("git ls-tree -r --name-only state", remote).stdout.split()
            self.assertEqual(files, ["ledger.jsonl"])
            log = sh("git log --format=%s state", remote).stdout.splitlines()
            self.assertEqual(len(log), 2)
            self.assertNotIn("app.py", files)  # code never lands on the state branch


if __name__ == "__main__":
    unittest.main()
