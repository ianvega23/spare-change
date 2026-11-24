"""Load and validate config.toml. Anything invalid stops the run before it touches money."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import guard

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
    buy_trigger_cents: int = 5_000
    monthly_cap_cents: int = guard.DEFAULT_MONTHLY_CAP_CENTS
    limits: guard.Limits = field(default_factory=guard.Limits)


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


_TOP_KEYS = {"enabled", "dry_run", "alpaca_env", "multiplier", "buy_trigger", "monthly_cap",
             "allow_stocks", "limits"}
_LIMIT_KEYS = {"min_holdings", "max_etf_weight", "max_stock_weight", "max_stocks_total"}


def from_dict(raw: dict) -> Config:
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    raw_limits = raw.get("limits", {})
    if set(raw_limits) - _LIMIT_KEYS:
        raise ConfigError(f"unknown limit(s): {', '.join(sorted(set(raw_limits) - _LIMIT_KEYS))}")
    for key in ("enabled", "dry_run", "allow_stocks"):
        if key in raw and not isinstance(raw[key], bool):
            raise ConfigError(f"{key} must be true or false")
    alpaca_env = raw.get("alpaca_env", "paper")
    if alpaca_env not in ("paper", "live"):
        raise ConfigError('alpaca_env must be "paper" or "live"')

    limits = guard.Limits(allow_stocks=raw.get("allow_stocks", False),
                          **{k: raw_limits[k] for k in _LIMIT_KEYS if k in raw_limits})
    cfg = Config(
        enabled=raw.get("enabled", True),
        dry_run=raw.get("dry_run", True),
        alpaca_env=alpaca_env,
        multiplier=raw.get("multiplier", 1),
        buy_trigger_cents=dollars_to_cents(raw.get("buy_trigger", 50), "buy_trigger"),
        monthly_cap_cents=dollars_to_cents(raw.get("monthly_cap", 200), "monthly_cap"),
        limits=limits,
    )
    errors = guard.validate_settings(buy_trigger_cents=cfg.buy_trigger_cents,
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
