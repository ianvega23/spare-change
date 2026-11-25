# Changelog

## 0.1.0 (unreleased)

The first release: everything in `docs/PLAN.md` phases 1 to 10. Defaults are dry run and Alpaca paper.

- Money engine: round-ups in integer cents, 1x/2x/3x multiplier, weekly buy of the whole pot,
  monthly cap, split toward the targets with new money only, append-only ledger, idempotent orders.
- Guardrails in plain code: allowlist of 15 ETFs, opt-in S&P 500 stocks (`allowlist/sp500.csv`),
  per-holding and total caps, small vs big changes, no selling.
- Brokers: Alpaca (paper and live) and Public (live only, $5 minimum, orders locked behind the
  go-live gate).
- Banks: Teller over mutual TLS, Plaid as fallback.
- GitHub Actions jobs `invest`, `brain` and `bot`, each with its own environment and keys, sharing a
  `state` branch.
- Telegram bot: onboarding, signed Approve/Skip proposals, `/status`, `/pause`, `/resume`, Sunday update.
- Claude allocator with schema-enforced output, output checks, one retry, cost tracking and a
  monthly budget; Haiku for the weekly summary and merchant categories.
- Shadow all-VTI portfolio.
- Evals: 60 profiles, 50 injection attacks, offline CI gate, opt-in live runs.
- `python -m spare setup`: guided setup of config, GitHub environments and secrets, and a ruleset
  that locks `main` so the `bot` and `brain` jobs can't change the code the `invest` job runs.
- `spare enroll` answers only requests addressed to `127.0.0.1`/`localhost` (blocks DNS rebinding).
- `python -m spare doctor`: checks the setup without touching money.
