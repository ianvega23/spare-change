"""`spare setup`: a guided first-time setup, run on your computer in your private copy.

It asks which bank and broker you use, writes the safe starting config (dry run, paper), creates
the GitHub environments, stores each secret in the one environment whose job needs it, and turns
the jobs on last. Secrets are typed with echo off and handed to `gh secret set` on stdin: they are
never printed, logged, written to a file or put on a command line. It's safe to run again: secrets
that are already set are skipped unless you ask to replace them.

It never turns off dry run and never touches live trading. That stays behind `spare doctor
--go-live` after the two-week paper run.
"""
from __future__ import annotations

import getpass
import json
import secrets as pysecrets
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .bot.onboard import dump_toml
from .core import config as config_mod
from .core.state import State


@dataclass(frozen=True)
class Secret:
    name: str
    env: str
    prompt: str
    kind: str = "text"  # text | file (the file's contents) | generated


# Each secret lives only in the environment of the job that uses it (docs/PLAN.md section 6).
BANK_SECRETS = {
    "teller": [Secret("TELLER_ACCESS_TOKEN", "money", "Teller access token (from `spare enroll`)"),
               Secret("TELLER_CERT_PEM", "money", "Path to your Teller certificate.pem", "file"),
               Secret("TELLER_KEY_PEM", "money", "Path to your Teller private_key.pem", "file")],
    "plaid": [Secret("PLAID_CLIENT_ID", "money", "Plaid client id"),
              Secret("PLAID_SECRET", "money", "Plaid secret"),
              Secret("PLAID_ACCESS_TOKEN", "money", "Plaid access token for your checking account")],
}
BROKER_SECRETS = {
    "alpaca": [Secret("APCA_API_KEY_ID", "money", "Alpaca PAPER key id (starts with PK)"),
               Secret("APCA_API_SECRET_KEY", "money", "Alpaca PAPER secret key")],
    "public": [Secret("PUBLIC_SECRET_KEY", "money", "Public API secret key (Settings > API)")],
}
TELEGRAM_SECRETS = [Secret("TELEGRAM_BOT_TOKEN", "telegram", "Telegram bot token from @BotFather"),
                    Secret("TELEGRAM_CHAT_ID", "telegram", "Your Telegram chat id (`spare telegram-chat-id`)"),
                    Secret("TELEGRAM_HMAC_KEY", "telegram", "button signing key", "generated")]
AI_SECRETS = [Secret("ANTHROPIC_API_KEY", "ai", "Anthropic API key (console.anthropic.com)")]
ENVIRONMENTS = ("money", "telegram", "ai")
ADMIN_ROLE_ID = 5  # GitHub's built-in "admin" repository role
MAIN_RULESET = {
    "name": "spare: protect main",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
    "bypass_actors": [{"actor_id": ADMIN_ROLE_ID, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
    "rules": [{"type": "deletion"}, {"type": "non_fast_forward"},
              {"type": "pull_request", "parameters": {
                  "required_approving_review_count": 0, "dismiss_stale_reviews_on_push": False,
                  "require_code_owner_review": False, "require_last_push_approval": False,
                  "required_review_thread_resolution": False}}],
}

Run = Callable[..., subprocess.CompletedProcess]
Ask = Callable[[str], str]


class SetupError(Exception):
    pass


def secrets_for(bank: str, broker: str, ai: bool) -> list[Secret]:
    return BANK_SECRETS[bank] + BROKER_SECRETS[broker] + TELEGRAM_SECRETS + (AI_SECRETS if ai else [])


def choose(ask: Ask, question: str, options: tuple[str, ...], default: str) -> str:
    while True:
        answer = ask(f"{question} [{'/'.join(options)}] (default {default}): ").strip().lower() or default
        if answer in options:
            return answer


def yes(ask: Ask, question: str, default: bool) -> bool:
    answer = ask(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


class GitHub:
    """The few `gh` calls setup needs. Values go on stdin, never on the command line."""

    def __init__(self, run: Run = subprocess.run, repo: str | None = None):
        self._run = run
        self.repo = repo

    def gh(self, *args: str, stdin: str | None = None) -> str:
        try:
            r = self._run(["gh", *args], input=stdin, capture_output=True, text=True,
                          encoding="utf-8", check=False)
        except FileNotFoundError:
            raise SetupError("the GitHub CLI `gh` isn't installed: https://cli.github.com") from None
        if r.returncode != 0:
            # gh's error text never contains the value: it was sent on stdin.
            raise SetupError(f"gh {' '.join(a for a in args[:3])} failed: {(r.stderr or '').strip()[:300]}")
        return r.stdout

    def check_repo(self) -> dict:
        args = ["repo", "view", "--json", "nameWithOwner,visibility,isTemplate"]
        info = json.loads(self.gh(*(args + ([self.repo] if self.repo else []))))
        self.repo = info["nameWithOwner"]
        return info

    def ensure_environment(self, env: str) -> None:
        """Create the environment and limit it to the main branch."""
        policy = {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
        self.gh("api", "-X", "PUT", f"repos/{self.repo}/environments/{env}", "--input", "-",
                stdin=json.dumps(policy))
        existing = json.loads(self.gh("api", f"repos/{self.repo}/environments/{env}/deployment-branch-policies"))
        if not any(p.get("name") == "main" for p in existing.get("branch_policies", [])):
            self.gh("api", "-X", "POST", f"repos/{self.repo}/environments/{env}/deployment-branch-policies",
                    "--input", "-", stdin=json.dumps({"name": "main", "type": "branch"}))

    def protect_main(self) -> bool:
        """Block direct pushes to the default branch. Returns False if it was already protected.

        The bot and brain jobs can write to the repo (for the state branch). Without this rule they
        could also change the code on main, which the invest job runs with your bank and broker keys.
        Repository admins (you) can still push; the jobs' GitHub token can't.
        """
        existing = json.loads(self.gh("api", f"repos/{self.repo}/rulesets"))
        if any(r.get("name") == MAIN_RULESET["name"] for r in existing):
            return False
        self.gh("api", "-X", "POST", f"repos/{self.repo}/rulesets", "--input", "-",
                stdin=json.dumps(MAIN_RULESET))
        return True

    def secret_names(self, env: str) -> set[str]:
        data = json.loads(self.gh("api", f"repos/{self.repo}/environments/{env}/secrets"))
        return {s["name"] for s in data.get("secrets", [])}

    def set_secret(self, env: str, name: str, value: str) -> None:
        self.gh("secret", "set", name, "--env", env, "--repo", self.repo, stdin=value)

    def set_variable(self, name: str, value: str) -> None:
        self.gh("variable", "set", name, "--body", value, "--repo", self.repo)


# Files that `spare enroll` and the Teller dashboard leave in teller/, offered as defaults.
DEFAULT_FILES = {"TELLER_ACCESS_TOKEN": Path("teller", "token.txt"),
                 "TELLER_CERT_PEM": Path("teller", "certificate.pem"),
                 "TELLER_KEY_PEM": Path("teller", "private_key.pem")}


def clean_path(text: str) -> Path:
    """A pasted path: Windows "Copy as path" adds quotes, and drag-and-drop may too."""
    return Path(text.strip().strip('"').strip("'")).expanduser()


def read_value(secret: Secret, ask_secret: Ask, ask: Ask, base: Path = Path(".")) -> str:
    if secret.kind == "generated":
        return pysecrets.token_hex(32)
    default = base / DEFAULT_FILES[secret.name] if secret.name in DEFAULT_FILES else None
    if secret.kind == "text" and default is not None and default.is_file():
        if yes(ask, f"  Use the token saved in {DEFAULT_FILES[secret.name]}?", True):
            return default.read_text(encoding="utf-8").strip()
    if secret.kind == "file":
        hint = f" (Enter for {DEFAULT_FILES[secret.name]})" if default is not None and default.is_file() else ""
        answer = ask(f"{secret.prompt}{hint}: ")
        path = default if hint and not answer.strip() else clean_path(answer)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise SetupError(f"can't read {path}: {e.strerror}") from None
        if "-----BEGIN" not in text:
            raise SetupError(f"{path} doesn't look like a PEM file")
        return text
    value = ""
    while not value.strip():
        value = ask_secret(f"{secret.prompt} (hidden): ")
    return value.strip()


def write_config(state: State, choices: dict) -> Path:
    """Merge the choices into state/config.toml, keeping your other settings. Always dry run."""
    state.ensure()
    existing = {}
    if state.config_path.exists():
        existing = tomllib.loads(state.config_path.read_text(encoding="utf-8"))
    merged = {**existing, **choices}
    if existing.get("dry_run") is False:
        merged["dry_run"] = False  # setup never flips this either way once you've gone live
    else:
        merged["dry_run"] = True
    merged.setdefault("alpaca_env", "paper")
    config_mod.from_dict(merged)  # refuses anything invalid before writing
    state.config_path.write_text(dump_toml(merged), encoding="utf-8")
    return state.config_path


def run(state_dir: str, *, ask: Ask = input, ask_secret: Ask = getpass.getpass,
        github: GitHub | None = None, out=print, replace: bool = False) -> int:
    out("Spare Change setup. Nothing here places an order: you start in dry run on paper.\n")
    gh = github or GitHub()
    try:
        info = gh.check_repo()
    except SetupError as e:
        out(f"Stopped: {e}\nRun this inside your own copy of the repository, signed in with `gh auth login`.")
        return 1
    if info.get("visibility", "").upper() != "PRIVATE":
        out(f"Stopped: {info['nameWithOwner']} is {info.get('visibility', 'not private').lower()}. "
            "Your state branch will hold your ledger, so use a private copy (README: Use your own copy privately).")
        return 1
    out(f"Repository: {gh.repo} (private)\n")

    bank = choose(ask, "Bank link", ("teller", "plaid"), "teller")
    broker = choose(ask, "Broker", ("alpaca", "public"), "alpaca")
    if broker == "public":
        out("  Public has no paper account, so every Public order is real money. You'll run dry for two\n"
            "  weeks first, and orders stay locked until the go-live checklist passes.")
    ai = yes(ask, "Use Claude for the weekly allocation review and summary (about $0.05 a week)?", True)
    choices = {"bank": bank, "broker": broker}
    if not ai:
        choices["ai_monthly_budget"] = "0.00"
    path = write_config(State(state_dir), choices)
    out(f"\nSaved {path} (dry run). Copy it to your state branch with the rest of state/.\n")

    needed = secrets_for(bank, broker, ai)
    try:
        for env in ENVIRONMENTS:
            if env == "ai" and not ai:
                continue
            gh.ensure_environment(env)
            have = gh.secret_names(env)
            out(f"Environment `{env}` (main branch only):")
            for s in (s for s in needed if s.env == env):
                if s.name in have and not replace:
                    out(f"  ok    {s.name} already set")
                    continue
                if s.kind != "generated" and not yes(ask, f"  Set {s.name} now?", True):
                    out(f"  skip  {s.name}: set it later with `gh secret set {s.name} --env {env}`")
                    continue
                gh.set_secret(env, s.name, read_value(s, ask_secret, ask))
                out(f"  set   {s.name}")
        added = gh.protect_main()
        out("Main branch: " + ("now protected" if added else "already protected")
            + " (the jobs can't change the code your money job runs).")
    except SetupError as e:
        out(f"\nStopped: {e}")
        if any(w in str(e).lower() for w in ("environment", "ruleset", "404", "403")):
            out("On GitHub Free, environment secrets and branch rules only work in public repositories; a private copy "
                "needs GitHub Pro, Team or Enterprise. "
                "Spare Change keeps each key in its own environment on purpose, so it won't fall back "
                "to repository-wide secrets, and won't turn the jobs on with main unprotected.")
        return 1

    if yes(ask, "\nTurn the scheduled jobs on now (repository variable SPARE_ENABLED = true)?", False):
        gh.set_variable("SPARE_ENABLED", "true")
        out("SPARE_ENABLED is on. The invest job runs on weekdays at 15:00 UTC, in dry run.")
    else:
        out("Jobs stay off. Turn them on later with `gh variable set SPARE_ENABLED --body true`.")

    out("\nNext:")
    steps = []
    if bank == "teller":
        steps.append("Link your bank: python -m spare enroll (then run setup again to store the token)")
    steps.append("Onboard over Telegram: python -m spare onboard (your goals, multiplier, buy day, cap)")
    steps.append("Check everything: python -m spare doctor")
    steps.append("Run Actions > invest > Run workflow once and read the result with python -m spare status")
    for i, step in enumerate(steps, 1):
        out(f"  {i}. {step}")
    return 0


def main(argv: list[str], out=print) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="spare setup", description=__doc__.splitlines()[0])
    p.add_argument("--state", default="state", help="your local state folder")
    p.add_argument("--repo", default=None, help="OWNER/NAME (default: the repo in this folder)")
    p.add_argument("--replace", action="store_true", help="ask again for secrets that are already set")
    args = p.parse_args(argv)
    if not sys.stdin.isatty():
        out("setup is interactive: run it in a terminal.")
        return 2
    if shutil.which("gh") is None:
        out("Install the GitHub CLI first (https://cli.github.com) and run `gh auth login`.")
        return 2
    return run(args.state, github=GitHub(repo=args.repo), out=out, replace=args.replace)
