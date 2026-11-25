"""A small Telegram Bot API client, standard library only.

The bot token is part of every URL, so URLs are never logged or put in errors.
"""
from __future__ import annotations

import json
from typing import Callable

from ..core.http import NetworkError, Transport, make_transport

API = "https://api.telegram.org"


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, transport: Transport | None = None, timeout: float = 35):
        if not token or ":" not in token:
            raise ValueError("TELEGRAM_BOT_TOKEN looks wrong (BotFather gives `123456:ABC...`)")
        self._token = token
        self._transport = transport or make_transport(timeout=timeout)

    def __repr__(self) -> str:
        return "Telegram()"

    def call(self, method: str, **params) -> dict | list | bool:
        url = f"{API}/bot{self._token}/{method}"
        body = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        try:
            resp = self._transport("POST", url, {"Content-Type": "application/json"}, body)
        except NetworkError as e:
            raise TelegramError(f"can't reach Telegram ({e})") from None
        try:
            data = json.loads(resp.body or b"{}")
        except ValueError:
            data = {}
        if resp.status != 200 or not data.get("ok"):
            raise TelegramError(f"{method} failed (HTTP {resp.status}: {str(data.get('description', ''))[:200]})")
        return data["result"]

    def send(self, chat_id: int | str, text: str, buttons: list[tuple[str, str]] | None = None) -> dict:
        markup = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]} if buttons else None
        return self.call("sendMessage", chat_id=chat_id, text=text[:4000], reply_markup=markup,
                         disable_web_page_preview=True)

    def updates(self, offset: int | None, timeout: int = 0) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=timeout,
                         allowed_updates=["message", "callback_query"])

    def answer(self, callback_id: str, text: str) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])


def chat_of(update: dict) -> int | None:
    """The chat an update came from, for messages and button taps."""
    msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    return (msg.get("chat") or {}).get("id")


SleepFn = Callable[[float], None]
