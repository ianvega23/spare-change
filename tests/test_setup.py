"""`spare setup` against a fake `gh`. No network, no real GitHub."""
import json
import pathlib
import re
import subprocess
import tomllib
import unittest

from spare import setup
from spare.core.state import State
from tests.helpers import TempDirCase

ROOT = pathlib.Path(__file__).resolve().parents[1]
SECRET_VALUE = "sk-very-secret-value"


class FakeGh:
    """Pretends to be the gh CLI, keeps secrets per environment, records every call."""

    def __init__(self, visibility="PRIVATE", existing=None, fail_on=None):
        self.calls = []
        self.visibility = visibility
        self.secrets = {e: set(v) for e, v in (existing or {}).items()}
        self.values = {}
        self.envs = {}
        self.variables = {}
        self.rulesets = []
        self.fail_on = fail_on

    def __call__(self, cmd, input=None, capture_output=True, text=True, check=False, encoding=None):
        assert encoding == "utf-8"  # gh speaks UTF-8; Windows would otherwise decode as cp1252
        self.calls.append((cmd, input))
        args = cmd[1:]
        ok = lambda out="": subprocess.CompletedProcess(cmd, 0, out, "")
        if self.fail_on and self.fail_on in " ".join(args):
            return subprocess.CompletedProcess(cmd, 1, "", "HTTP 403: Environments are not available")
        if args[:2] == ["repo", "view"]:
            return ok(json.dumps({"nameWithOwner": "me/spare", "visibility": self.visibility}))
        if args[:1] == ["api"] and any(a.endswith("/rulesets") for a in args):
            if "POST" in args:
                self.rulesets.append(json.loads(input))
                return ok("{}")
            return ok(json.dumps([{"name": r["name"]} for r in self.rulesets]))
        if args[:1] == ["api"]:
            path = next(a for a in args[1:] if a.startswith("repos/"))
            env = path.split("/")[4]
            if path.endswith("/deployment-branch-policies"):
                if "POST" in args:
                    self.envs.setdefault(env, set()).add(json.loads(input)["name"])
                    return ok("{}")
                return ok(json.dumps({"branch_policies": [{"name": n} for n in self.envs.get(env, ())]}))
            if path.endswith("/secrets"):
                return ok(json.dumps({"secrets": [{"name": n} for n in self.secrets.get(env, ())]}))
            self.envs.setdefault(env, set())
            return ok("{}")
        if args[:2] == ["secret", "set"]:
            name, env = args[2], args[args.index("--env") + 1]
            self.secrets.setdefault(env, set()).add(name)
            self.values[(env, name)] = input
            return ok()
        if args[:2] == ["variable", "set"]:
            self.variables[args[2]] = args[args.index("--body") + 1]
            return ok()
        raise AssertionError(f"unexpected gh call {args}")


class Script:
    """Answers prompts in order, and fails loudly if asked something unexpected."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"no answer scripted for: {prompt}")
        return self.answers.pop(0)


class SetupTests(TempDirCase):
    def go(self, gh, answers, secret_answers=(), **kw):
        out = []
        self.ask, self.ask_secret = Script(*answers), Script(*secret_answers)
        code = setup.run(self.dir, ask=self.ask, ask_secret=self.ask_secret,
                         github=setup.GitHub(run=gh), out=out.append, **kw)
        return code, "\n".join(out)

    def pem(self, name):
        path = pathlib.Path(self.dir, name)
        path.write_text("-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n")
        return str(path)

    def test_full_setup_teller_alpaca(self):
        gh = FakeGh()
        code, out = self.go(gh, [
            "teller", "", "",                      # teller, alpaca, AI yes
            "y", "y", self.pem("c.pem"), "y", self.pem("k.pem"), "y", "y",   # money
            "y", "y",                        # telegram token and chat id (HMAC is generated)
            "y",                             # ai
            "n"],                            # leave the jobs off
            [SECRET_VALUE, "PKKEY", "alpaca-secret", "123:bot", "42", "sk-ant"])
        self.assertEqual(code, 0, out)
        self.assertEqual(gh.secrets["money"], {"TELLER_ACCESS_TOKEN", "TELLER_CERT_PEM", "TELLER_KEY_PEM",
                                               "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"})
        self.assertEqual(gh.secrets["telegram"], {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_HMAC_KEY"})
        self.assertEqual(gh.secrets["ai"], {"ANTHROPIC_API_KEY"})
        self.assertRegex(gh.values[("telegram", "TELEGRAM_HMAC_KEY")], r"^[0-9a-f]{64}$")
        self.assertIn("BEGIN CERTIFICATE", gh.values[("money", "TELLER_CERT_PEM")])
        self.assertEqual({e: p for e, p in gh.envs.items()}, {e: {"main"} for e in setup.ENVIRONMENTS})
        self.assertEqual(gh.variables, {})
        cfg = tomllib.loads(State(self.dir).config_path.read_text())
        self.assertEqual((cfg["bank"], cfg["broker"], cfg["dry_run"], cfg["alpaca_env"]),
                         ("teller", "alpaca", True, "paper"))

    def test_secrets_never_on_a_command_line_or_in_output(self):
        gh = FakeGh(existing={"money": {"TELLER_ACCESS_TOKEN", "TELLER_CERT_PEM", "TELLER_KEY_PEM"}})
        code, out = self.go(gh, ["teller", "public", "n", "y", "n", "n", "y"], [SECRET_VALUE])
        self.assertEqual(code, 0, out)
        self.assertEqual(gh.values[("money", "PUBLIC_SECRET_KEY")], SECRET_VALUE)
        for cmd, _ in gh.calls:
            self.assertNotIn(SECRET_VALUE, " ".join(cmd))
        self.assertNotIn(SECRET_VALUE, out)
        self.assertNotIn(SECRET_VALUE, State(self.dir).config_path.read_text())

    def test_public_warns_real_money_and_stays_dry(self):
        gh = FakeGh()
        code, out = self.go(gh, ["plaid", "public", "n"] + ["n"] * 6 + ["n"])
        self.assertEqual(code, 0, out)
        self.assertIn("every Public order is real money", out)
        cfg = tomllib.loads(State(self.dir).config_path.read_text())
        self.assertEqual((cfg["broker"], cfg["dry_run"], cfg["ai_monthly_budget"]), ("public", True, "0.00"))
        self.assertNotIn("ai", gh.envs)  # no AI, no ai environment

    def test_rerun_skips_what_is_set_and_turns_jobs_on(self):
        names = {s.env: set() for s in setup.secrets_for("teller", "alpaca", True)}
        for s in setup.secrets_for("teller", "alpaca", True):
            names[s.env].add(s.name)
        gh = FakeGh(existing=names)
        code, out = self.go(gh, ["teller", "", "", "y"])
        self.assertEqual(code, 0, out)
        self.assertEqual(gh.values, {})
        self.assertEqual(gh.variables, {"SPARE_ENABLED": "true"})
        self.assertIn("ok    APCA_API_KEY_ID already set", out)

    def test_keeps_your_other_settings(self):
        state = State(self.dir).ensure()
        state.config_path.write_text('multiplier = 2\nbuy_day = "friday"\n')
        self.go(FakeGh(), ["teller", "", "n"] + ["n"] * 7 + ["n"])
        cfg = tomllib.loads(state.config_path.read_text())
        self.assertEqual((cfg["multiplier"], cfg["buy_day"], cfg["dry_run"]), (2, "friday", True))

    def test_protects_main_once_and_before_the_jobs_go_on(self):
        gh = FakeGh(existing={s.env: {s.name} for s in setup.secrets_for("teller", "alpaca", True)})
        gh.secrets = {}
        for s in setup.secrets_for("teller", "alpaca", True):
            gh.secrets.setdefault(s.env, set()).add(s.name)
        code, out = self.go(gh, ["teller", "", "", "y"])
        self.assertEqual(code, 0, out)
        self.assertEqual(len(gh.rulesets), 1)
        rule = gh.rulesets[0]
        self.assertEqual((rule["target"], rule["enforcement"]), ("branch", "active"))
        self.assertEqual(rule["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])
        self.assertEqual({r["type"] for r in rule["rules"]}, {"deletion", "non_fast_forward", "pull_request"})
        # Only the admin role may bypass: never an app or integration (the jobs' GitHub token).
        self.assertEqual([(a["actor_type"], a["actor_id"]) for a in rule["bypass_actors"]],
                         [("RepositoryRole", setup.ADMIN_ROLE_ID)])
        self.assertIn("Main branch: now protected", out)
        order = [" ".join(c) for c, _ in gh.calls]
        self.assertLess(next(i for i, c in enumerate(order) if "rulesets" in c and "POST" in c),
                        next(i for i, c in enumerate(order) if "SPARE_ENABLED" in c))
        code, out = self.go(gh, ["teller", "", "", "y"])  # run again: no second ruleset
        self.assertEqual(len(gh.rulesets), 1)
        self.assertIn("Main branch: already protected", out)

    def test_jobs_stay_off_when_main_cant_be_protected(self):
        names = {}
        for s in setup.secrets_for("teller", "alpaca", True):
            names.setdefault(s.env, set()).add(s.name)
        gh = FakeGh(existing=names, fail_on="rulesets")
        code, out = self.go(gh, ["teller", "", ""])
        self.assertEqual(code, 1, out)
        self.assertEqual(gh.variables, {})
        self.assertIn("branch rules only work in public repositories", out)

    def test_refuses_a_public_repo(self):
        code, out = self.go(FakeGh(visibility="PUBLIC"), [])
        self.assertEqual(code, 1)
        self.assertIn("private copy", out)

    def test_no_fallback_when_environments_are_unavailable(self):
        code, out = self.go(FakeGh(fail_on="environments/money"), ["teller", "", ""])
        self.assertEqual(code, 1)
        self.assertIn("won't fall back", out)

    def test_bad_pem_stops(self):
        bad = pathlib.Path(self.dir, "x.pem")
        bad.write_text("not a cert")
        code, out = self.go(FakeGh(), ["teller", "", "n", "y", "y", str(bad)], [SECRET_VALUE])
        self.assertEqual(code, 1)
        self.assertIn("doesn't look like a PEM", out)


class MatchesWorkflowsTest(unittest.TestCase):
    def test_every_secret_goes_to_the_environment_its_workflow_reads(self):
        used = {}
        for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
            text = wf.read_text()
            env = re.search(r"environment: (\w+)", text)
            for name in re.findall(r"secrets\.(\w+)", text):
                used.setdefault(name, set()).add(env.group(1) if env else None)
        every = {s for group in (*setup.BANK_SECRETS.values(), *setup.BROKER_SECRETS.values(),
                                 setup.TELEGRAM_SECRETS, setup.AI_SECRETS) for s in group}
        for s in every:
            self.assertIn(s.env, used.get(s.name, set()), s.name)
        self.assertEqual({n for n, envs in used.items() if envs - {"evals"}}, {s.name for s in every})


if __name__ == "__main__":
    unittest.main()
