"""`spare bank-check`: read-only look at your linked bank, to confirm the setup once."""
from __future__ import annotations

from datetime import date, timedelta

from .core.bank_teller import TellerBank
from .core.engine import round_up_cents


def bank_check(bank, today: date | None = None, days: int = 14, out=print) -> int:
    today = today or date.today()
    if isinstance(bank, TellerBank):
        bank.checking_account_id()
        for a in bank.accounts():
            mark = "  <- used" if a.get("id") == bank.account_id else ""
            out(f"Account {a.get('id')}: {a.get('institution', {}).get('name', '?')} "
                f"{a.get('subtype', '?')} ending {a.get('last_four', '????')} ({a.get('status', '?')}){mark}")
        account = bank.checking_account_id()
        out(f"Rounding up account {account}, purchases signed {bank.spending_sign}.")
    txns = bank.transactions((today - timedelta(days=days)).isoformat(), today.isoformat())
    out(f"\nLast {days} days: {len(txns)} transactions. Check that purchases show as 'purchase':")
    for t in sorted(txns, key=lambda t: t.date, reverse=True)[:15]:
        kind = "purchase" if t.amount_cents > 0 else "money in / skipped"
        state = "posted" if t.posted else "pending (counted once posted)"
        roundup = f"+${round_up_cents(t.amount_cents) / 100:.2f}" if t.amount_cents > 0 else ""
        out(f"  {t.date}  {t.description[:28]:<28} ${abs(t.amount_cents) / 100:>9.2f}  {kind:<18} {state} {roundup}")
    out("\nIf purchases show as 'money in', set teller_spending_sign = \"positive\" in config.toml.")
    return 0
