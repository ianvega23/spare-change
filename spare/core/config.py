"""Load and validate config.toml. Anything invalid stops the run before it touches money."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import guard
from .engine import WEEKDAYS

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALLOWLIST = REPO_ROOT / "allowlist" / "etfs.toml"
DEFAULT_SP500 = REPO_ROOT / "allowlist" / "sp500.csv"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    enabled: bool = True
    dry_run: bool = True
    alpaca_env: str = "paper"
    multiplier: int = 1
    buy_weekday: int = 0  # 0 = Monday ... 4 = Friday
    monthly_cap_cents: int = guard.DEFAULT_MONTHLY_CAP_CENTS
    limits: guard.Limits = field(default_factory=guard.Limits)
    bank: str = "teller"
    bank_environment: str = "production"
    bank_account_id: str = ""
    teller_spending_sign: str = "negative"
    allocator_model: str = "claude-opus-5-5"
    cheap_model: str = "claude-haiku-5-5"
    ai_monthly_budget_cents: int = 200
    send_merchants_to_ai: bool = True


def dollars_to_cents(value, name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a dollar amount")
    try:
        cents = Decimal(str(value)) * 100
    except InvalidOperation as e:
        raise ConfigError(f"{name} must be a dollar amount") from e
    if cents != cents.to_integral_value():
        raise ConfigError(f"{name} has fractions of a cent")
    return int(cents)


_TOP_KEYS = {"enabled", "dry_run", "alpaca_env", "multiplier", "buy_day", "monthly_cap",
             "allow_stocks", "limits", "bank", "bank_environment",
             "bank_account_id", "teller_spending_sign",
             "allocator_model", "cheap_model", "ai_monthly_budget",
             "send_merchants_to_ai"}
_LIMIT_KEYS = {"min_holdings", "max_etf_weight", "max_stock_weight", "max_stocks_total"}


def from_dict(raw: dict) -> Config:
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    raw_limits = raw.get("limits", {})
    if set(raw_limits) - _LIMIT_KEYS:
        raise ConfigError(f"unknown limit(s): {', '.join(sorted(set(raw_limits) - _LIMIT_KEYS))}")
    for key in ("enabled", "dry_run", "allow_stocks", "send_merchants_to_ai"):
        if key in raw and not isinstance(raw[key], bool):
            raise ConfigError(f"{key} must be true or false")
    alpaca_env = raw.get("alpaca_env", "paper")
    if alpaca_env not in ("paper", "live"):
        raise ConfigError('alpaca_env must be "paper" or "live"')

    buy_day = raw.get("buy_day", "monday")
    if not isinstance(buy_day, str) or buy_day.lower() not in WEEKDAYS:
        raise ConfigError("buy_day must be a weekday name, monday to friday")

    bank = raw.get("bank", "teller")
    if bank not in ("teller", "plaid"):
        raise ConfigError('bank must be "teller" or "plaid"')
    bank_env = raw.get("bank_environment", "production")
    if bank_env not in ("sandbox", "development", "production"):
        raise ConfigError("bank_environment must be sandbox, development or production")
    sign = raw.get("teller_spending_sign", "negative")
    if sign not in ("negative", "positive"):
        raise ConfigError('teller_spending_sign must be "negative" or "positive"')
    account = raw.get("bank_account_id", "")
    if not isinstance(account, str):
        raise ConfigError("bank_account_id must be text")

    models = {}
    for key, default in (("allocator_model", "claude-opus-5-5"), ("cheap_model", "claude-haiku-5-5")):
        value = raw.get(key, default)
        if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.\-]{2,63}", value):
            raise ConfigError(f"{key} must be a model id like {default}")
        models[key] = value
    ai_budget = dollars_to_cents(raw.get("ai_monthly_budget", "2.00"), "ai_monthly_budget")
    if not 0 <= ai_budget <= 10_000:
        raise ConfigError("ai_monthly_budget must be between $0 and $100")

    limits = guard.Limits(allow_stocks=raw.get("allow_stocks", False),
                          **{k: raw_limits[k] for k in _LIMIT_KEYS if k in raw_limits})
    cfg = Config(
        enabled=raw.get("enabled", True),
        dry_run=raw.get("dry_run", True),
        alpaca_env=alpaca_env,
        multiplier=raw.get("multiplier", 1),
        buy_weekday=WEEKDAYS.index(buy_day.lower()),
        monthly_cap_cents=dollars_to_cents(raw.get("monthly_cap", 200), "monthly_cap"),
        limits=limits,
        bank=bank,
        bank_environment=bank_env,
        bank_account_id=account,
        teller_spending_sign=sign,
        ai_monthly_budget_cents=ai_budget,
        send_merchants_to_ai=raw.get("send_merchants_to_ai", True),
        **models,
    )
    errors = guard.validate_settings(buy_weekday=cfg.buy_weekday,
                                     multiplier=cfg.multiplier,
                                     monthly_cap_cents=cfg.monthly_cap_cents)
    errors += guard.validate_limits(limits)
    if errors:
        raise ConfigError("; ".join(errors))
    return cfg


def load(path: str | Path) -> Config:
    try:
        with open(path, "rb") as f:
            return from_dict(tomllib.load(f))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e


def load_universe(etf_path: str | Path = DEFAULT_ALLOWLIST,
                  sp500_path: str | Path = DEFAULT_SP500) -> guard.Universe:
    with open(etf_path, "rb") as f:
        etfs = frozenset(tomllib.load(f))
    stocks: frozenset[str] = frozenset()
    if Path(sp500_path).exists():
        lines = Path(sp500_path).read_text(encoding="utf-8").splitlines()[1:]  # header row
        stocks = frozenset(line.split(",")[0].strip() for line in lines if line.strip())
    return guard.Universe(etfs=etfs, stocks=stocks)
