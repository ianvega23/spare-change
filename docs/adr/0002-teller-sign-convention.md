# ADR 0002: Make Teller's amount sign a setting, checked once with `spare bank-check`

**Status:** accepted (phase 3). Applies only to the Teller adapter; Plaid, the default since ADR 0005, signs purchases positive and needs no setting.

## Context

The plan asked to verify Teller's sign convention for checking-account transactions against its
docs. Teller's API reference (https://teller.io/docs/api, version 2020-10-12) describes `amount` only
as "the signed amount" and doesn't say whether a card purchase on a checking account is negative or
positive. Guessing wrong would round up deposits and refunds instead of purchases.

## Decision

- `teller_spending_sign` in `config.toml`, default `"negative"` (money out is negative).
- `spare bank-check` lists recent transactions with how each one is read ("purchase" or "money in")
  and its round-up, so the setting is confirmed once against your own Chase account before any buy.
- Independently of the sign, transactions typed `transfer`, `deposit`, `interest`, `dividend` or
  `payroll` never round up, and only `posted` transactions count.
- Plaid's convention is documented (positive = money out) and needs no setting.

## Consequences

The go-live checklist (phase 9) includes running `bank-check` on real data. If Teller documents the
sign later, the default can be fixed and this ADR updated.
