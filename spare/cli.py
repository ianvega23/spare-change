"""Command line: python -m spare <command>. Phase 1 ships `demo`; the rest arrive in later phases."""
from __future__ import annotations

import argparse
import os
import tempfile
from datetime import date, timedelta

from .core import config as config_mod
from .core import invest
from .core.ledger import Ledger
from .fake import FakeBank, FakeBroker

# An example allocation for the demo only. Not advice.
DEMO_TARGETS = {"VTI": 0.35, "VXUS": 0.20, "BND": 0.20, "SGOV": 0.15, "VNQ": 0.10}
DEMO_PRICES = {"VTI": 31_000, "VXUS": 6_800, "BND": 7_300, "SGOV": 10_050, "VNQ": 9_100}
LATER = {"onboard": 5, "invest": 4, "brain": 6, "bot": 5, "status": 4, "doctor": 4, "eval": 8}


def usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def demo(weeks: int, multiplier: int, buy_day: str, seed: int, ledger_path: str | None,
         out=print) -> Ledger:
    cfg = config_mod.from_dict({"dry_run": False, "multiplier": multiplier, "buy_day": buy_day})
    universe = config_mod.load_universe()
    start = date(2026, 1, 5)  # a Monday
    days = weeks * 7
    bank = FakeBank(start.isoformat(), days, seed=seed, per_day=(2, 5))  # ~25 purchases a week
    broker = FakeBroker(cash_cents=0, prices_cents=DEMO_PRICES)
    if ledger_path is None:
        ledger_path = os.path.join(tempfile.mkdtemp(prefix="spare-demo-"), "ledger.jsonl")
    ledger = Ledger(ledger_path)

    out(f"Spare Change demo: {weeks} weeks, fake bank and broker, {multiplier}x round-ups, "
        f"weekly buy on {buy_day.capitalize()}s, cap {usd(cfg.monthly_cap_cents)}/month.")
    out("Example allocation (not advice): "
        + ", ".join(f"{s} {w:.0%}" for s, w in DEMO_TARGETS.items()) + "\n")
    week_roundups = 0
    for d in range(days):
        day = start + timedelta(days=d)
        if day.day == 1 or d == 0:
            broker.deposit(cfg.monthly_cap_cents)  # the recurring monthly deposit you set up once
        if day.weekday() < 5:
            before = sum(e["cents"] for e in ledger.of_type("roundup"))
            result = invest.run(cfg, ledger, bank, broker, DEMO_TARGETS, universe, day.isoformat())
            week_roundups += sum(e["cents"] for e in ledger.of_type("roundup")) - before
            if result.status == "bought":
                total = sum(o.notional_cents for o in result.orders)
                out(f"  {day}  BUY {usd(total)}: "
                    + ", ".join(f"{o.symbol} {usd(o.notional_cents)}" for o in result.orders))
            elif result.status in ("cash_short", "invalid", "cap_reached"):
                out(f"  {day}  {result.status.replace('_', ' ')}: {'; '.join(result.errors) or 'holding the pot'}")
        if day.weekday() == 6:
            out(f"Week {d // 7 + 1:>2}: +{usd(week_roundups)} round-ups, pot {usd(ledger.pot_cents())}")
            week_roundups = 0

    holdings = ledger.holdings_cents()
    out(f"\nInvested {usd(sum(holdings.values()))} in {len(ledger.of_type('buy'))} orders; "
        f"pot {usd(ledger.pot_cents())}.")
    out("Holdings at cost: " + ", ".join(f"{s} {usd(c)}" for s, c in sorted(holdings.items())))
    out(f"Ledger: {ledger_path}")
    return ledger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spare", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("demo", help="simulate weeks of round-ups and buys with fake accounts")
    p.add_argument("--weeks", type=int, default=12)
    p.add_argument("--multiplier", type=int, default=1, choices=(1, 2, 3))
    p.add_argument("--buy-day", default="monday", help="weekday to invest the week's round-ups")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--ledger", default=None, help="ledger path (default: a temp file)")
    for name in LATER:
        sub.add_parser(name, help=f"coming in phase {LATER[name]}")
    args = parser.parse_args(argv)

    if args.command == "demo":
        try:
            demo(args.weeks, args.multiplier, args.buy_day, args.seed, args.ledger)
        except config_mod.ConfigError as e:
            parser.error(str(e))
        return 0
    print(f"`{args.command}` arrives in phase {LATER[args.command]} (see docs/PLAN.md).")
    return 2
