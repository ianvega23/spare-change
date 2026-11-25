"""One pass of the hourly bot job: expire, send the outbox, offer proposals, handle taps and commands.

Only the single chat in TELEGRAM_CHAT_ID is ever answered. Everyone else is ignored and counted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..core import config as config_mod
from ..core import proposals
from ..core.state import State
from .telegram import Telegram, TelegramError, chat_of

HELP = ("Commands: /status, /pause (stop buying), /resume, /profile (redo your goals).\n"
        "Big allocation changes arrive here with Approve and Skip buttons.")


@dataclass
class BotResult:
    sent: int = 0
    proposals_offered: int = 0
    decisions: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    ignored_other_chats: int = 0


def _bot_state(state: State) -> dict:
    return state._read_json("bot.json") or {}


def run_once(state_dir: str, telegram: Telegram, chat_id: int, hmac_key: bytes,
             now: datetime | None = None, status_text=None) -> BotResult:
    now = now or datetime.now(timezone.utc)
    state = State(state_dir).ensure()
    result = BotResult()
    cfg = config_mod.load(state.config_path) if state.config_path.exists() else config_mod.from_dict({})
    universe = config_mod.load_universe()

    for p in proposals.expire_old(state, now):
        telegram.send(chat_id, f"Proposal {p['id']} expired after 7 days, so it was skipped.")
        result.sent += 1

    for path in sorted((state.root / "outbox").glob("*.json")):
        message = json.loads(path.read_text(encoding="utf-8"))
        try:
            telegram.send(chat_id, message["text"])
        except TelegramError:
            break  # keep the rest for the next hour
        path.unlink()
        result.sent += 1

    for p in proposals.pending(state):
        if p.get("offered_at"):
            continue
        telegram.send(chat_id, proposals.describe(p),
                      buttons=[("Approve", proposals.button_data(hmac_key, p["id"], "a")),
                               ("Skip", proposals.button_data(hmac_key, p["id"], "s"))])
        state._write_json(f"proposals/{p['id']}.json", {**p, "offered_at": now.isoformat(timespec="seconds")})
        result.proposals_offered += 1

    bot = _bot_state(state)
    offset = bot.get("offset")
    for update in telegram.updates(offset):
        offset = update["update_id"] + 1
        if chat_of(update) != chat_id:
            result.ignored_other_chats += 1
            continue
        if "callback_query" in update:
            cq = update["callback_query"]
            parsed = proposals.parse_button(hmac_key, cq.get("data", ""))
            if not parsed:
                telegram.answer(cq["id"], "That button isn't valid.")
                result.decisions.append("forged")
                continue
            reply = proposals.decide(state, parsed[0], parsed[1], universe, cfg.limits, now)
            telegram.answer(cq["id"], reply)
            telegram.send(chat_id, reply)
            result.decisions.append(parsed[1])
            continue
        text = ((update.get("message") or {}).get("text") or "").strip().split()
        command = text[0].split("@")[0].lower() if text else ""
        result.commands.append(command)
        if command == "/pause":
            state._write_json("flags.json", {"paused": True, "at": now.isoformat(timespec="seconds")})
            reply = "Paused. No round-ups are collected and nothing is bought until /resume."
        elif command == "/resume":
            state._write_json("flags.json", {"paused": False, "at": now.isoformat(timespec="seconds")})
            reply = "Resumed. The next weekday run picks up from here."
        elif command == "/status":
            reply = status_text() if status_text else "Status isn't available."
        elif command == "/profile":
            reply = "To redo your goals, run `python -m spare onboard` on your computer."
        else:
            reply = HELP
        telegram.send(chat_id, reply)

    state._write_json("bot.json", {**bot, "offset": offset, "ignored_other_chats":
                                   bot.get("ignored_other_chats", 0) + result.ignored_other_chats})
    return result
