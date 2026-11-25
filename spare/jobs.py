"""The `invest`, `status` and `doctor` commands, wired to the state branch and env secrets."""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

from .core import config as config_mod
from .core import guard, invest
from .core.bank import BankError
from .core.broker import BrokerError
from .core.broker_alpaca import AlpacaBroker
from .core import shadow
from .core.state import State, StateError

STALE_RUN_DAYS = 3
SECRET_NAMES = {"teller": ("TELLER_ACCESS_TOKEN", "TELLER_CERT", "TELLER_KEY"),
                "plaid": ("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ACCESS_TOKEN"),
                "alpaca": ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")}
EXIT_OK, EXIT_FAILED = 0, 1


def usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def load_config(state: State) -> config_mod.Config:
    """Your settings from the state branch; missing means the safe defaults (dry run, paper)."""
    if state.config_path.exists():
        return config_mod.load(state.config_path)
    return config_mod.from_dict({})


def live_unlocked(cfg: config_mod.Config, state: State) -> bool:
    gate = state.go_live() or {}
    return cfg.alpaca_env == "live" and not cfg.dry_run and gate.get("passed") is True


def broker_from_env(cfg: config_mod.Config, state: State) -> AlpacaBroker:
    if cfg.alpaca_env == "live" and not live_unlocked(cfg, state):
        raise SystemExit("alpaca_env is live, but live trading needs dry_run = false and a passed "
                         "go-live checklist (state/go_live.json). Staying out of the market.")
    key, secret = os.environ.get("APCA_API_KEY_ID", ""), os.environ.get("APCA_API_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("APCA_API_KEY_ID and APCA_API_SECRET_KEY aren't set.")
    return AlpacaBroker(key, secret, env=cfg.alpaca_env, allow_live=live_unlocked(cfg, state))


def run_invest(state_dir: str, today: str | None = None, bank=None, broker=None, out=print) -> int:
    state = State(state_dir).ensure()
    day = today or datetime.now(timezone.utc).date().isoformat()
    try:
        cfg = load_config(state)
    except config_mod.ConfigError as e:
        out(f"Config is invalid, so nothing ran: {e}")
        state.record_run("config_invalid", day, ok=False)
        return EXIT_FAILED
    if not cfg.enabled or state.paused():
        out("Paused (kill switch). Nothing fetched, nothing bought.")
        state.record_run("paused", day, ok=True)
        return EXIT_OK

    ledger = state.ledger()
    try:
        if bank is None:
            from .core.sources import bank_from_env
            bank = bank_from_env(cfg)
        broker = broker or broker_from_env(cfg, state)
        targets = state.targets()
        result = invest.run(cfg, ledger, bank, broker, targets, config_mod.load_universe(), day)
    except (BankError, BrokerError, StateError) as e:
        out(f"Run failed: {e}")
        state.queue_message("alert", f"Spare Change run failed: {e}")
        state.record_run("error", day, ok=False)
        return EXIT_FAILED

    mode = f"{cfg.alpaca_env}{', dry run' if cfg.dry_run else ''}"
    out(f"{day} ({mode}): {result.new_roundups} new round-ups, pot {usd(result.pot_cents)}, "
        f"status {result.status}")
    for symbol, cents in result.plan.items():
        out(f"  {'would buy' if result.status == 'dry_run' else 'buy'} {symbol} {usd(cents)}")
    for message in result.errors:
        out(f"  alert: {message}")
        state.queue_message("alert", message)
    if result.status == "bought":
        total = sum(o.notional_cents for o in result.orders)
        state.queue_message("buy", f"Bought {usd(total)} across {len(result.orders)} funds: "
                            + ", ".join(f"{o.symbol} {usd(o.notional_cents)}" for o in result.orders))
    if result.status == "no_targets":
        out("  No approved allocation yet, so the pot keeps growing (onboarding arrives in phase 5).")
    record_valuation(state, broker, result, day, out)
    ok = result.status != "invalid"
    state.record_run(result.status, day, ok=ok)
    return EXIT_OK if ok else EXIT_FAILED


def record_valuation(state: State, broker, result, day: str, out=print) -> None:
    """For the weekly update only: mirror new buys into the shadow portfolio and note market values.
    A failure here never fails the run, because no money depends on it."""
    try:
        vti = broker.latest_price(shadow.SHADOW_SYMBOL)
        if result.status == "bought" and vti:
            state.append_jsonl("shadow.jsonl", shadow.entry(day, sum(o.notional_cents for o in result.orders), vti))
        state._write_json("valuation.json", {"day": day, "positions_cents": broker.positions_cents(),
                                             "vti_price": vti})
    except (BrokerError, AttributeError, OSError) as e:
        out(f"  note: couldn't record market values ({e})")


def status(state_dir: str, out=print) -> int:
    state = State(state_dir)
    ledger = state.ledger()
    cfg = load_config(state)
    holdings = ledger.holdings_cents()
    out(f"Mode: {cfg.alpaca_env}{', dry run' if cfg.dry_run else ''}"
        f"{', PAUSED' if (state.paused() or not cfg.enabled) else ''}")
    out(f"Pot: {usd(ledger.pot_cents())} (buys every {config_mod.WEEKDAYS[cfg.buy_weekday].capitalize()})")
    out(f"Invested: {usd(sum(holdings.values()))} in {len(ledger.buys())} orders; last buy {ledger.last_buy_date() or 'never'}")
    if holdings:
        out("Holdings at cost: " + ", ".join(f"{s} {usd(c)}" for s, c in sorted(holdings.items())))
    targets = state.targets()
    out("Targets: " + (", ".join(f"{s} {w:.0%}" for s, w in targets.items()) if targets else "none approved yet"))
    last = state.last_run()
    out(f"Last run: {last['day']} {last['status']}{'' if last['ok'] else ' (FAILED)'}" if last else "Last run: never")
    for alert in ledger.of_type("alert")[-3:]:
        out(f"Recent alert ({alert['at'][:10]}): {alert.get('message', '')}")
    return EXIT_OK


def doctor(state_dir: str, today: date | None = None, environ=None, out=print) -> int:
    """Checks the setup without touching money. Exit 1 if anything would stop a run."""
    environ = os.environ if environ is None else environ
    today = today or datetime.now(timezone.utc).date()
    state = State(state_dir)
    problems, warnings = [], []

    try:
        cfg = load_config(state)
        out(f"ok    config ({'state/config.toml' if state.config_path.exists() else 'defaults'}): "
            f"{cfg.alpaca_env}, {'dry run' if cfg.dry_run else 'placing orders'}")
    except config_mod.ConfigError as e:
        problems.append(f"config: {e}")
        cfg = config_mod.from_dict({})

    for group in (cfg.bank, "alpaca"):
        names = SECRET_NAMES[group]
        if group == "teller" and cfg.bank_environment == "sandbox":
            names = names[:1]
        missing = [n for n in names if not environ.get(n)]
        (problems.append(f"secrets: {', '.join(missing)} not set") if missing
         else out(f"ok    {group} secrets present"))

    try:
        targets = state.targets()
        if not targets:
            warnings.append("no approved allocation yet (the pot grows; nothing is bought)")
        else:
            errors = guard.validate_targets(targets, config_mod.load_universe(), cfg.limits)
            (problems.append("targets: " + "; ".join(errors)) if errors else out("ok    targets pass every guardrail"))
    except StateError as e:
        problems.append(str(e))

    if cfg.alpaca_env == "live" and not live_unlocked(cfg, state):
        problems.append("live is set but locked: needs dry_run = false and a passed go-live checklist")
    if state.paused() or not cfg.enabled:
        warnings.append("paused: the kill switch is on")

    last = state.last_run()
    if not last:
        warnings.append("invest has never run")
    else:
        age = (today - date.fromisoformat(last["day"])).days
        if age > STALE_RUN_DAYS:
            warnings.append(f"last invest run was {age} days ago; GitHub may have disabled the schedule")
        if not last.get("ok"):
            warnings.append(f"last run failed ({last.get('status')})")

    for w in warnings:
        out(f"warn  {w}")
    for p in problems:
        out(f"FAIL  {p}")
    out("All good." if not problems else f"{len(problems)} problem(s) to fix before the next run.")
    return EXIT_OK if not problems else EXIT_FAILED
