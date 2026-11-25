"""Command line: python -m spare <command>."""
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
LATER = {"eval": 8}


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


def first_mix(state_dir: str, environ=None, out=print):
    """Onboarding's first proposal: the Claude allocator if ANTHROPIC_API_KEY is set here,
    otherwise (or if it fails) the plain starter mix. Either way it waits for your Approve."""
    environ = os.environ if environ is None else environ

    def propose(profile: dict) -> dict:
        from .brain import starter
        if environ.get("ANTHROPIC_API_KEY"):
            try:
                from .brain import job
                from .brain.llm import LLMError
            except ImportError:
                out("The anthropic package isn't installed (pip install --require-hashes -r "
                    "requirements-ai.txt), so you get the starter mix.")
                return starter.propose(profile)
            try:
                return job.first_proposal(state_dir, profile, environ=environ)
            except (LLMError, ImportError) as e:
                out(f"The allocator didn't produce a mix ({e}), so you get the starter mix.")
        return starter.propose(profile)
    return propose


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spare", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("demo", help="simulate weeks of round-ups and buys with fake accounts")
    p.add_argument("--weeks", type=int, default=12)
    p.add_argument("--multiplier", type=int, default=1, choices=(1, 2, 3))
    p.add_argument("--buy-day", default="monday", help="weekday to invest the week's round-ups")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--ledger", default=None, help="ledger path (default: a temp file)")
    p = sub.add_parser("onboard", help="the one-time setup conversation over Telegram (on your computer)")
    p.add_argument("--state", default=os.environ.get("SPARE_STATE_DIR", "state"))
    p = sub.add_parser("bot", help="one pass of the hourly bot job: send, offer proposals, handle taps")
    p.add_argument("--state", default=os.environ.get("SPARE_STATE_DIR", "state"))
    sub.add_parser("telegram-chat-id", help="print the chat id of whoever just messaged your bot")
    p = sub.add_parser("invest", help="one run of the money path (what the weekday job runs)")
    p.add_argument("--state", default=os.environ.get("SPARE_STATE_DIR", "state"))
    p.add_argument("--date", default=None, help="pretend today is this ISO date (testing)")
    p = sub.add_parser("brain", help="the weekly allocation review (what the Sunday job runs)")
    p.add_argument("--state", default=os.environ.get("SPARE_STATE_DIR", "state"))
    p = sub.add_parser("status", help="pot, holdings, targets and the last run")
    p.add_argument("--state", default=os.environ.get("SPARE_STATE_DIR", "state"))
    p = sub.add_parser("doctor", help="check the setup without touching money")
    p.add_argument("--state", default=os.environ.get("SPARE_STATE_DIR", "state"))
    p.add_argument("--go-live", action="store_true", help="run the paper go-live checklist (phase 9)")
    p = sub.add_parser("enroll", help="link your bank with Teller Connect (runs on your computer)")
    p.add_argument("--app-id", default=os.environ.get("TELLER_APPLICATION_ID", ""))
    p.add_argument("--environment", default="development", choices=("sandbox", "development", "production"))
    p.add_argument("--relink", default=None, metavar="ENROLLMENT_ID", help="repair an existing link")
    p.add_argument("--dir", default="teller", help="where to save the token (git-ignored)")
    p = sub.add_parser("bank-check", help="read-only look at your linked bank account")
    p.add_argument("--config", default="config.toml")
    sub.add_parser("alpaca-check", help="read-only check of your Alpaca paper account")
    p = sub.add_parser("paper-order", help="place one small Alpaca paper buy and reconcile it")
    p.add_argument("--symbol", default="VTI")
    p.add_argument("--dollars", type=int, default=5)
    for name in LATER:
        sub.add_parser(name, help=f"coming in phase {LATER[name]}")
    args = parser.parse_args(argv)

    if args.command == "demo":
        try:
            demo(args.weeks, args.multiplier, args.buy_day, args.seed, args.ledger)
        except config_mod.ConfigError as e:
            parser.error(str(e))
        return 0
    if args.command in ("onboard", "bot", "telegram-chat-id"):
        from .bot import telegram as tg
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            parser.error("set TELEGRAM_BOT_TOKEN (from BotFather)")
        client = tg.Telegram(token)
        if args.command == "telegram-chat-id":
            chats = {tg.chat_of(u) for u in client.updates(None)} - {None}
            print("Chat ids that messaged your bot: " + (", ".join(map(str, chats)) or
                  "none yet. Send your bot any message, then run this again."))
            return 0
        chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not chat.lstrip("-").isdigit():
            parser.error("set TELEGRAM_CHAT_ID (see `spare telegram-chat-id`)")
        if args.command == "onboard":
            from .bot import onboard
            onboard.run(args.state, onboard.TelegramAsk(client, int(chat)), first_mix(args.state))
            print("Your first proposal reaches Telegram on the next bot run (hourly), or now with "
                  "`python -m spare bot` if TELEGRAM_HMAC_KEY is set here too. Push the state "
                  "folder to your state branch so the jobs see it.")
            return 0
        key = os.environ.get("TELEGRAM_HMAC_KEY", "")
        if len(key) < 32:
            parser.error("set TELEGRAM_HMAC_KEY to a random secret of 32+ characters")
        from . import jobs
        from .bot import relay
        def status_text():
            lines = []
            jobs.status(args.state, out=lines.append)
            return "\n".join(lines)
        r = relay.run_once(args.state, client, int(chat), key.encode(), status_text=status_text)
        print(f"Sent {r.sent}, offered {r.proposals_offered} proposal(s), decisions {r.decisions or 'none'}, "
              f"commands {r.commands or 'none'}, ignored {r.ignored_other_chats} message(s) from other chats.")
        return 0
    if args.command == "brain":
        from .brain import job
        return job.run_brain(args.state)
    if args.command in ("invest", "status", "doctor"):
        from . import jobs
        if args.command == "invest":
            return jobs.run_invest(args.state, args.date)
        if args.command == "status":
            return jobs.status(args.state)
        if args.go_live:
            print("The go-live checklist arrives in phase 9 (see docs/PLAN.md).")
            return 2
        return jobs.doctor(args.state)
    if args.command == "enroll":
        if not args.app_id:
            parser.error("pass --app-id or set TELLER_APPLICATION_ID (from your Teller dashboard)")
        from . import enroll
        return enroll.run(args.app_id, args.environment, args.dir, args.relink)
    if args.command == "bank-check":
        from . import bank_tools
        from .core.bank import BankError
        from .core.sources import bank_from_env
        cfg = config_mod.load(args.config) if os.path.exists(args.config) else config_mod.from_dict({})
        try:
            return bank_tools.bank_check(bank_from_env(cfg))
        except BankError as e:
            print(f"Bank error: {e}")
            return 1
    if args.command in ("alpaca-check", "paper-order"):
        from . import alpaca_tools
        from .core.broker import BrokerError
        broker = alpaca_tools.paper_broker_from_env()
        try:
            if args.command == "alpaca-check":
                return alpaca_tools.alpaca_check(broker)
            return alpaca_tools.paper_order(broker, args.symbol.upper(), args.dollars * 100)
        except BrokerError as e:
            print(f"Alpaca error: {e}")
            return 1
    print(f"`{args.command}` arrives in phase {LATER[args.command]} (see docs/PLAN.md).")
    return 2
