"""Big allocation changes wait for your Approve in Telegram. This module holds that flow.

A proposal is a file in state/proposals/. Its Approve/Skip buttons carry the proposal id and an
HMAC signed with a key only the bot job has, so a forged tap is rejected. Proposals expire after
7 days, and expired or unanswered means Skip: nothing big ever happens by default. An approved
proposal is re-checked against every guardrail before it becomes targets.json.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from . import guard
from .state import State

EXPIRY = timedelta(days=7)
ACTIONS = {"a": "approve", "s": "skip"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sign(key: bytes, proposal_id: str, action: str) -> str:
    return hmac.new(key, f"{proposal_id}:{action}".encode(), hashlib.sha256).hexdigest()[:20]


def button_data(key: bytes, proposal_id: str, action: str) -> str:
    """Fits Telegram's 64-byte callback_data limit."""
    return f"{action}:{proposal_id}:{sign(key, proposal_id, action)}"


def parse_button(key: bytes, data: str) -> tuple[str, str] | None:
    """(proposal_id, 'approve' | 'skip') for a genuine button, else None."""
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] not in ACTIONS:
        return None
    action, pid, sig = parts
    if not hmac.compare_digest(sig, sign(key, pid, action)):
        return None
    return pid, ACTIONS[action]


def create(state: State, targets: dict[str, float], *, summary: str, reasons: dict[str, str],
           source: str, now: datetime | None = None) -> dict:
    now = now or _now()
    proposal = {"id": secrets.token_hex(4), "targets": targets, "summary": summary[:1500],
                "reasons": {s: str(r)[:300] for s, r in reasons.items()}, "source": source,
                "status": "pending", "created_at": now.isoformat(timespec="seconds"),
                "expires_at": (now + EXPIRY).isoformat(timespec="seconds")}
    (state.root / "proposals").mkdir(parents=True, exist_ok=True)
    state._write_json(f"proposals/{proposal['id']}.json", proposal)
    return proposal


def load(state: State, proposal_id: str) -> dict | None:
    if not proposal_id.isalnum() or len(proposal_id) > 16:
        return None
    return state._read_json(f"proposals/{proposal_id}.json")


def pending(state: State) -> list[dict]:
    folder = state.root / "proposals"
    if not folder.exists():
        return []
    out = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
    return [p for p in out if p.get("status") == "pending"]


def _record(state: State, proposal: dict, status: str, now: datetime, note: str = "") -> None:
    proposal = {**proposal, "status": status, "decided_at": now.isoformat(timespec="seconds")}
    state._write_json(f"proposals/{proposal['id']}.json", proposal)
    with open(state.root / "decisions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "decision", "proposal": proposal["id"], "status": status,
                            "at": proposal["decided_at"], "note": note}, sort_keys=True) + "\n")


def expire_old(state: State, now: datetime | None = None) -> list[dict]:
    now = now or _now()
    expired = [p for p in pending(state) if datetime.fromisoformat(p["expires_at"]) <= now]
    for p in expired:
        _record(state, p, "expired", now, "no answer in 7 days, so skipped")
    return expired


def decide(state: State, proposal_id: str, action: str, universe: guard.Universe,
           limits: guard.Limits, now: datetime | None = None) -> str:
    """Apply a verified tap. Returns a short message for the user."""
    now = now or _now()
    proposal = load(state, proposal_id)
    if not proposal:
        return "That proposal doesn't exist."
    if proposal.get("status") != "pending":
        return f"Already {proposal.get('status')}."
    if datetime.fromisoformat(proposal["expires_at"]) <= now:
        _record(state, proposal, "expired", now, "tapped after it expired")
        return "That proposal expired, so it was skipped."
    if action == "skip":
        _record(state, proposal, "skipped", now)
        return "Skipped. Your current allocation stays."
    errors = guard.validate_targets(proposal["targets"], universe, limits)
    if errors:
        _record(state, proposal, "rejected", now, "; ".join(errors))
        return "Not applied: it breaks a guardrail (" + "; ".join(errors)[:150] + ")."
    state._write_json("targets.json", {"targets": proposal["targets"], "proposal": proposal["id"],
                                       "approved_at": now.isoformat(timespec="seconds")})
    _record(state, proposal, "approved", now)
    return "Approved. New money follows this allocation from the next buy."


def describe(proposal: dict) -> str:
    lines = ["Proposed allocation (needs your OK):"]
    for symbol, weight in sorted(proposal["targets"].items(), key=lambda kv: -kv[1]):
        reason = proposal.get("reasons", {}).get(symbol, "")
        lines.append(f"• {symbol} {weight:.0%}" + (f": {reason}" if reason else ""))
    if proposal.get("summary"):
        lines += ["", proposal["summary"]]
    lines += ["", "Approve or Skip. No answer in 7 days means Skip."]
    return "\n".join(lines)
