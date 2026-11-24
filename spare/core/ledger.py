"""Append-only JSONL ledger. Every round-up and buy is one line; lines are never edited.

The pot, the month's spending, seen transaction ids and holdings are all derived from it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

EVENT_TYPES = frozenset({"roundup", "buy", "targets_changed", "proposal", "decision",
                         "alert", "reconciled"})


class LedgerError(Exception):
    pass


class Ledger:
    def __init__(self, path: str):
        self.path = path
        self._events = self._load()

    def _load(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        events = []
        with open(self.path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as e:
                    raise LedgerError(f"{self.path}:{n}: not valid JSON") from e
                if event.get("type") not in EVENT_TYPES:
                    raise LedgerError(f"{self.path}:{n}: unknown event type {event.get('type')!r}")
                events.append(event)
        return events

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def append(self, type: str, at: str | None = None, **data) -> dict:
        if type not in EVENT_TYPES:
            raise LedgerError(f"unknown event type {type!r}")
        event = {"type": type, "at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 **data}
        line = json.dumps(event, sort_keys=True, separators=(",", ":"))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._events.append(event)
        return event

    # ---- derived views -------------------------------------------------------------------

    def of_type(self, type: str) -> list[dict]:
        return [e for e in self._events if e["type"] == type]

    def seen_txn_ids(self) -> set[str]:
        return {e["txn_id"] for e in self.of_type("roundup")}

    def pot_cents(self) -> int:
        """Round-ups collected minus everything invested."""
        return (sum(e["cents"] for e in self.of_type("roundup"))
                - sum(e["notional_cents"] for e in self.of_type("buy")))

    def invested_in_month_cents(self, month: str) -> int:
        """month is 'YYYY-MM'."""
        return sum(e["notional_cents"] for e in self.of_type("buy") if e["date"].startswith(month))

    def holdings_cents(self) -> dict[str, int]:
        """Cost basis per symbol (v1 never sells)."""
        out: dict[str, int] = {}
        for e in self.of_type("buy"):
            out[e["symbol"]] = out.get(e["symbol"], 0) + e["notional_cents"]
        return out

    def last_batch_id(self) -> str:
        buys = self.of_type("buy")
        return buys[-1]["batch_id"] if buys else ""

    def unbatched_roundup_ids(self) -> list[str]:
        """Round-ups recorded since the last buy. With the previous batch id they define the
        next batch, so a retry after a crash produces the same order ids."""
        last_buy = max((i for i, e in enumerate(self._events) if e["type"] == "buy"), default=-1)
        return [e["txn_id"] for e in self._events[last_buy + 1:] if e["type"] == "roundup"]

    def batch_ids(self) -> set[str]:
        return {e["batch_id"] for e in self.of_type("buy")}

    def client_order_ids(self) -> set[str]:
        return {e["client_order_id"] for e in self.of_type("buy")}
