"""The files on the `state` branch, which is how the three jobs talk to each other.

    state/
      config.toml      your settings (optional; missing = safe defaults: dry run, paper)
      ledger.jsonl     append-only money record (invest job writes)
      targets.json     the approved allocation {"VTI": 0.35, ...} (brain/bot jobs write)
      flags.json       {"paused": true} from the bot's /pause (kill switch)
      go_live.json     written by `spare doctor --go-live` after the paper checklist passes
      last_run.json    when invest last finished, and how
      outbox/          messages for the bot job to send to Telegram
      proposals/       big changes waiting for Approve/Skip (brain writes, bot decides)
      decisions.jsonl  every answer and every small change applied on its own
      telemetry.jsonl  tokens, cost and latency of each AI call (brain job writes)
      brain.json       when the allocator last ran, and the last automatic change
      shadow.jsonl     each buy mirrored into all-VTI, with VTI's price then (invest job writes)
      valuation.json   market value of each holding and VTI's price at the last run (invest job)
      categories.json  merchant -> spending category cache (weekly update writes)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .ledger import Ledger

MAX_MESSAGE_CHARS = 1_000


class StateError(Exception):
    pass


class State:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def ensure(self) -> "State":
        (self.root / "outbox").mkdir(parents=True, exist_ok=True)
        return self

    @property
    def config_path(self) -> Path:
        return self.root / "config.toml"

    def ledger(self) -> Ledger:
        return Ledger(str(self.root / "ledger.jsonl"))

    def _read_json(self, name: str):
        path = self.root / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise StateError(f"{name} is not valid JSON") from e

    def targets(self) -> dict[str, float]:
        """The approved allocation, or {} if nothing is approved yet. The guard re-checks it."""
        data = self._read_json("targets.json")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise StateError("targets.json must be an object of symbol to weight")
        weights = data.get("targets", data)  # accept {"targets": {...}, ...} or a bare mapping
        if not isinstance(weights, dict):
            raise StateError("targets.json must map symbols to weights")
        return dict(weights)

    def paused(self) -> bool:
        flags = self._read_json("flags.json") or {}
        return bool(flags.get("paused")) if isinstance(flags, dict) else True  # unreadable = paused

    def go_live(self) -> dict | None:
        return self._read_json("go_live.json")

    def last_run(self) -> dict | None:
        return self._read_json("last_run.json")

    def _write_json(self, name: str, data) -> None:
        path = self.root / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash never leaves half a file

    def record_run(self, status: str, day: str, ok: bool) -> None:
        self._write_json("last_run.json", {"status": status, "day": day, "ok": ok,
                                           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    def queue_message(self, kind: str, text: str) -> Path:
        """Leave a message for the bot job. Totals and fund names only, never account details."""
        self.ensure()
        now = datetime.now(timezone.utc)
        existing = len(list((self.root / "outbox").glob("*.json")))
        name = f"outbox/{now.strftime('%Y%m%dT%H%M%S')}-{existing:03d}-{kind}.json"
        self._write_json(name, {"kind": kind, "text": text[:MAX_MESSAGE_CHARS],
                                "at": now.isoformat(timespec="seconds")})
        return self.root / name

    def append_jsonl(self, name: str, data: dict) -> None:
        with open(self.root / name, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, sort_keys=True) + "\n")

    def read_jsonl(self, name: str) -> list[dict]:
        """Every readable line; a torn last line (a crash mid-write) is skipped."""
        path = self.root / name
        if not path.exists():
            return []
        out = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def outbox(self) -> list[dict]:
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((self.root / "outbox").glob("*.json"))]
