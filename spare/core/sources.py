"""Build the configured bank source from config plus secrets in environment variables."""
from __future__ import annotations

import os

from .bank import Bank
from .bank_plaid import PlaidBank
from .bank_teller import TellerBank
from .config import Config

TELLER_ENV = ("TELLER_ACCESS_TOKEN", "TELLER_CERT", "TELLER_KEY")
PLAID_ENV = ("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ACCESS_TOKEN")


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} isn't set.")
    return value


def bank_from_env(cfg: Config) -> Bank:
    if cfg.bank == "plaid":
        return PlaidBank(_env("PLAID_CLIENT_ID"), _env("PLAID_SECRET"), _env("PLAID_ACCESS_TOKEN"),
                         environment="sandbox" if cfg.bank_environment == "sandbox" else "production",
                         account_id=cfg.bank_account_id or None)
    sandbox = cfg.bank_environment == "sandbox"
    return TellerBank(_env("TELLER_ACCESS_TOKEN"),
                      cert_path=os.environ.get("TELLER_CERT") or (None if sandbox else _env("TELLER_CERT")),
                      key_path=os.environ.get("TELLER_KEY") or (None if sandbox else _env("TELLER_KEY")),
                      account_id=cfg.bank_account_id or None, spending_sign=cfg.teller_spending_sign,
                      environment=cfg.bank_environment)
