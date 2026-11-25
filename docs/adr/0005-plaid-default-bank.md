# ADR 0005: Plaid becomes the default bank link

**Status:** accepted (2026-11-25)

## Context

The plan picked Teller for Chase, with Plaid as the fallback. In November 2026 Teller had no public
way to create a developer account: teller.io has no sign-up or login link, its docs never give one,
and teller.io/signup and teller.io/dashboard return Not Found. A third-party guide (Rutter) still
points to teller.io/signup. New users can't get the application id or certificate the Teller path needs.

Plaid's Trial plan (support.plaid.com, "What is the Plaid Trial plan?", read 2026-11-25) is free,
self-serve for personal use in the US and Canada, uses real Production data, allows 10 Items,
includes Chase among its OAuth institutions, and is auto-approved after identity verification.

## Decision

- `spare enroll` links through **Plaid Link** by default (`spare/enroll_plaid.py`): it creates a link
  token for the `transactions` product only, serves Link on 127.0.0.1 with the same nonce and Host
  checks as the Teller page, exchanges the public token and saves the access token to
  `plaid/token.txt` (owner-only, git-ignored). `--relink` opens Link in update mode with the existing
  token, so nothing else changes.
- `spare setup` offers `plaid` first and uses `plaid/token.txt` as the default for `PLAID_ACCESS_TOKEN`.
- `docs/SETUP.md` uses Plaid. Teller stays fully supported for people who already have an account
  (`spare enroll --app-id ...`).

## Consequences

- Plaid's Trial plan caps you at 10 Items in total, and removing one doesn't free a slot. One copy
  needs one.
- Plaid's convention (positive amount = money out) is fixed, so there's no sign setting to confirm.
