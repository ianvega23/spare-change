"""A plain starter allocation from your horizon sentence, for when the AI allocator isn't available.

It picks one of three fixed mixes. Like every proposal, it goes through the guardrails and needs
your Approve. Placeholders to show how the software works, not advice.
"""
from __future__ import annotations

import re

MIXES = {
    "conservative": {"BND": 0.30, "SGOV": 0.25, "VTI": 0.20, "VTIP": 0.15, "VXUS": 0.10},
    "balanced": {"VTI": 0.35, "BND": 0.25, "VXUS": 0.20, "SGOV": 0.10, "VNQ": 0.10},
    "growth": {"VTI": 0.35, "VXUS": 0.25, "VB": 0.15, "BND": 0.15, "VWO": 0.10},
}
REASONS = {"BND": "US bonds for steadier value", "SGOV": "Treasury bills, close to cash",
           "VTI": "the whole US stock market", "VTIP": "inflation-protected Treasuries",
           "VXUS": "stocks outside the US", "VNQ": "real estate", "VB": "smaller US companies",
           "VWO": "emerging markets"}


def years_from(text: str) -> int | None:
    numbers = [int(n) for n in re.findall(r"\d+", text or "")]
    return max(numbers) if numbers else None


def pick_mix(profile: dict) -> str:
    years = years_from(profile.get("horizon", ""))
    if years is not None and years < 3:
        return "conservative"
    if years is not None and years >= 10:
        return "growth"
    return "balanced"


def propose(profile: dict) -> dict:
    name = pick_mix(profile)
    targets = MIXES[name]
    return {"targets": dict(targets), "reasons": {s: REASONS[s] for s in targets},
            "summary": f"A {name} starter mix based on your time horizon. The weekly AI "
                       "review can suggest changes later.", "source": f"starter:{name}"}
