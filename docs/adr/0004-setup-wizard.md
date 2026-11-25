# ADR 0004: A setup wizard that stores secrets through `gh`, never on disk

**Status:** accepted (phase 10)

## Context

Setting up by hand meant creating three GitHub environments, limiting each to `main`, and pasting
nine or more secrets into the right one. Putting a key in the wrong environment silently breaks the
"three jobs, three sets of secrets" design, and it's the step new users get wrong.

## Decision

`python -m spare setup` runs on your computer inside your private copy and uses the GitHub CLI:

- It **refuses a public repository**, because the `state` branch holds your ledger.
- It asks for the bank (Plaid by default, or Teller for existing accounts), the broker (Alpaca or Public) and whether to use Claude,
  then merges those into `state/config.toml`, keeping your other settings. It always leaves
  `dry_run = true` and `alpaca_env = "paper"` unless you had already gone live.
- It creates the `money`, `telegram` and `ai` environments, each limited to the `main` branch, and
  stores each secret only in the environment of the job that reads it. A test checks this mapping
  against the workflow files.
- Secrets are typed with echo off (`getpass`) and sent to `gh secret set` on **stdin**: never printed,
  logged, written to a file or put on a command line. PEM files are read and sent the same way.
  `TELEGRAM_HMAC_KEY` is generated.
- It's safe to re-run: secrets already set are skipped (`--replace` asks again).
- It turns the jobs on (`SPARE_ENABLED`) only if you say yes, as the last step.
- If environments aren't available it stops and says so. GitHub's docs (read 2026-11-25): "If you
  are using GitHub Free, environment secrets are only available in public repositories", and
  deployment branch rules in private repositories need GitHub Pro or Team. It **does not fall back** to repository-wide secrets, which would give every
  job every key.

## Consequences

- The wizard needs `gh` and a signed-in account; the manual steps stay in the README for anyone
  who prefers them.
- Linking the bank (`spare enroll`) and onboarding over Telegram (`spare onboard`) stay separate
  commands, because both need a browser or the phone. The wizard lists them as next steps.
