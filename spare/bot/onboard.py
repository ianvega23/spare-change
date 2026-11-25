"""`spare onboard`: the one-time setup conversation, run on your computer over Telegram.

Asks three questions in your own words (goals, risk, horizon), confirms the settings, saves
profile.toml and config.toml into your state folder, and creates the first proposal.
"""
from __future__ import annotations

import time
import tomllib
from typing import Callable

from ..core import config as config_mod
from ..core import proposals
from ..core.state import State
from .telegram import Telegram, chat_of

QUESTIONS = [("goals", "What are you investing for? One sentence is plenty."),
             ("risk", "How do you feel about risk? For example: can you live with big drops?"),
             ("horizon", "When will you need this money?")]
MAX_ANSWER = 300

Ask = Callable[[str, list[tuple[str, str]] | None], str]


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{s}"'


def dump_toml(data: dict) -> str:
    lines = [f"{k} = {toml_value(v)}" for k, v in data.items() if not isinstance(v, dict)]
    for k, v in data.items():
        if isinstance(v, dict) and v:
            lines += ["", f"[{k}]"] + [f"{kk} = {toml_value(vv)}" for kk, vv in v.items()]
    return "\n".join(lines) + "\n"


def converse(ask: Ask) -> tuple[dict, dict]:
    profile = {}
    for key, question in QUESTIONS:
        answer = ""
        while not answer.strip():
            answer = ask(question, None)
        profile[key] = answer.strip()[:MAX_ANSWER]
    settings = {
        "multiplier": int(ask("Round-up multiplier? 2x doubles every round-up.",
                              [("1x", "1"), ("2x", "2"), ("3x", "3")])),
        "buy_day": ask("Which weekday should the week's round-ups be invested?",
                       [(d[:3].capitalize(), d) for d in config_mod.WEEKDAYS]),
        "monthly_cap": int(ask("Most to invest in one month?",
                               [("$100", "100"), ("$200", "200"), ("$300", "300"), ("$500", "500")])),
        "allow_stocks": ask("Allow individual S&P 500 stocks (max 10% each)? Funds only is the default.",
                            [("Funds only", "no"), ("Allow stocks", "yes")]) == "yes",
    }
    return profile, settings


def save(state: State, profile: dict, settings: dict) -> config_mod.Config:
    state.ensure()
    existing = {}
    if state.config_path.exists():
        existing = tomllib.loads(state.config_path.read_text(encoding="utf-8"))
    merged = {**existing, **settings}
    cfg = config_mod.from_dict(merged)  # refuses anything invalid before writing
    state.config_path.write_text(dump_toml(merged), encoding="utf-8")
    (state.root / "profile.toml").write_text(
        "# Your own words, sent to the allocator as data, never as instructions.\n" + dump_toml(profile),
        encoding="utf-8")
    return cfg


def run(state_dir: str, ask: Ask, propose: Callable[[dict], dict], out=print) -> dict:
    state = State(state_dir)
    profile, settings = converse(ask)
    save(state, profile, settings)
    first = propose(profile)
    proposal = proposals.create(state, first["targets"], summary=first["summary"],
                                reasons=first["reasons"], source=first["source"])
    out(f"Saved profile.toml and config.toml in {state.root}, and proposal {proposal['id']}.")
    return proposal


class TelegramAsk:
    """Asks over Telegram and long-polls for the answer from your chat only."""

    def __init__(self, telegram: Telegram, chat_id: int, poll_seconds: int = 30,
                 sleep: Callable[[float], None] = time.sleep):
        self.t, self.chat_id, self.poll, self.sleep = telegram, chat_id, poll_seconds, sleep
        self.offset = None
        for u in telegram.updates(None):  # skip anything sent before onboarding started
            self.offset = u["update_id"] + 1

    def __call__(self, question: str, options: list[tuple[str, str]] | None) -> str:
        self.t.send(self.chat_id, question, buttons=[(label, f"o:{value}") for label, value in options]
                    if options else None)
        allowed = {v for _, v in options} if options else None
        while True:
            for u in self.t.updates(self.offset, timeout=self.poll):
                self.offset = u["update_id"] + 1
                if chat_of(u) != self.chat_id:
                    continue
                if "callback_query" in u and allowed:
                    value = (u["callback_query"].get("data") or "").removeprefix("o:")
                    self.t.answer(u["callback_query"]["id"], "Got it")
                    if value in allowed:
                        return value
                elif "message" in u and not allowed:
                    return (u["message"].get("text") or "").strip()
                elif "message" in u and allowed:
                    self.t.send(self.chat_id, "Tap one of the buttons above.")
