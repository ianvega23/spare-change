# Spare Change

A free, open-source, self-hosted round-up investor. It rounds each card purchase up to the
next dollar, saves the spare change, and once a week it invests that week's total in fractional
shares of a diversified portfolio in your own brokerage account. An AI allocator suggests the mix from
the goals you describe in your own words, and plain code decides whether that suggestion is allowed.

It is built for developers who are comfortable with API keys and GitHub secrets. There is no
hosted service: you run it on GitHub Actions' free tier with your own Chase (via Teller),
Alpaca and Telegram accounts.

> **Status:** phases 1 to 7 of 10 (money engine, Alpaca paper adapter, Teller bank link, the weekday GitHub Actions job, the Telegram bot, the Claude allocator and the weekly update) are built and tested. Nothing here trades real money yet. The defaults are dry run
> and Alpaca paper trading. See the [roadmap](#roadmap).

## Safety design

**Claude proposes, code decides.** The AI never holds bank or broker keys, never places orders,
and never sees your transactions when it picks an allocation.

- **Three jobs, three sets of secrets.** `invest` (bank + broker keys, standard library only),
  `brain` (Anthropic key only) and `bot` (Telegram token only) share nothing but JSON files on a
  `state` branch. A fully hijacked AI job still can't reach your money.
- **Guardrails in plain code** (`spare/core/guard.py`), checked when a proposal is made and again
  before every buy:

  | Rule | Default |
  |---|---|
  | Universe | ~15 allowlisted ETFs; S&P 500 stocks only if you opt in |
  | Minimum holdings | 5 |
  | Max per ETF / per stock / all stocks | 35% / 10% / 30% |
  | Weights | whole percents, summing to 100% |
  | Small change (applied on its own) | shift of 10 points or less among current holdings, at most once a week |
  | Big change (needs your Approve in Telegram) | adding or removing a holding, or a bigger shift |
  | Selling | blocked in v1; rebalancing uses new money only |
  | Monthly cap | $200 invested per calendar month |
  | When to buy | once a week, on your chosen weekday, whatever the amount (under $1 carries over) |
  | Round-up multiplier | 1x, 2x or 3x |

  Your config can tighten these limits but can't loosen them.
- **Append-only ledger and idempotent orders.** Every round-up and buy is a line in a JSONL ledger.
  Order ids are derived from the round-ups they spend, so a retried run can't buy twice.
- **Kill switch, dry run and paper by default.** Going live needs explicit settings plus a passed
  two-week paper-trading checklist.

The full design, threat model and trade-offs are in [`docs/PLAN.md`](docs/PLAN.md) and
[`docs/REVIEW.md`](docs/REVIEW.md).

## Try it

Python 3.11 or newer, nothing to install:

```bash
python -m spare demo                    # 12 simulated weeks with a fake bank and broker
python -m spare demo --multiplier 2     # 2x round-ups
python -m unittest discover -s tests    # the test suite, offline, a few seconds
```

The demo shows each weekday run: round-ups going into the pot, and a split buy across the
example portfolio every Monday:

```text
  2026-01-12  BUY $7.77: VTI $2.82, VXUS $1.80, BND $1.80, SGOV $1.35
Week  2: +$9.57 round-ups, pot $6.48
  2026-01-19  BUY $12.24: VTI $3.38, VXUS $2.45, BND $2.45, SGOV $1.84, VNQ $2.12
Week  3: +$13.19 round-ups, pot $7.43
  2026-01-26  BUY $12.70: VTI $4.45, VXUS $2.54, BND $2.54, SGOV $1.90, VNQ $1.27
```

## How the code is laid out

| Path | What it does |
|---|---|
| `spare/core/engine.py` | Round-ups in integer cents, multiplier, weekly buy day, monthly cap, split with a $1 minimum per order |
| `spare/core/guard.py` | Every guardrail as a pure function: allowlist, caps, weights, small vs big change, no sells |
| `spare/core/ledger.py` | Append-only JSONL ledger; pot, holdings and seen transactions are derived from it |
| `spare/core/invest.py` | One run of the money path: reconcile, fetch, round up, check, split, validate, order, record |
| `spare/core/broker_alpaca.py` | Alpaca Trading API over stdlib HTTP: cash, clock, assets, notional orders, order list; paper unless explicitly unlocked |
| `spare/core/bank_teller.py` | Teller over mutual TLS: your checking account's posted purchases, re-link alerts; `bank_plaid.py` is the fallback |
| `spare/core/state.py` | Files on the `state` branch: ledger, approved targets, pause flag, outbox for the bot |
| `spare/jobs.py` | `invest`, `status` and `doctor` commands |
| `spare/core/proposals.py` | Big changes: proposals with HMAC-signed Approve/Skip buttons, 7-day expiry, guard re-check on approve |
| `spare/bot/` | Telegram client, the hourly relay (outbox, buttons, /status /pause /resume) and onboarding |
| `spare/core/shadow.py` | The shadow all-VTI portfolio: each buy mirrored at VTI's price that day |
| `spare/brain/` | The weekly update (`summary.py`, `categorize.py`) and the Claude allocator: model adapter (`llm.py`), versioned prompts, output checks and retry (`allocator.py`), cost tracking and budget (`telemetry.py`), the Sunday `brain` job |
| `spare/core/config.py` | Loads `config.toml`; refuses anything invalid or looser than the hard limits |
| `spare/fake.py` | Fake bank and broker for tests and the demo |
| `allowlist/etfs.toml` | The 15-ETF default universe (a placeholder, not advice) |

`spare/core` uses only the Python standard library and never imports the AI or bot code. Tests
enforce both rules.

## Link your bank (Teller)

1. Create a free Teller developer account, note the application id, and download your client
   certificate and key (keep them out of the repo; `teller/` is git-ignored).
2. `python -m spare enroll --app-id app_...` opens Teller Connect on `127.0.0.1` and saves the
   read-only access token to `teller/token.txt`.
3. Check that purchases are read correctly:

```bash
export TELLER_ACCESS_TOKEN=$(cat teller/token.txt) TELLER_CERT=teller/certificate.pem TELLER_KEY=teller/private_key.pem
python -m spare bank-check   # read-only: your accounts and the last 14 days, each marked purchase or money in
```

If a password change or MFA breaks the link, the weekly job keeps the pot and alerts you to run
`spare enroll --relink <enrollment id>`.

## Check your Alpaca paper account

With your **paper** keys in environment variables (never in a file you commit):

```bash
export APCA_API_KEY_ID=...  APCA_API_SECRET_KEY=...
python -m spare alpaca-check              # read-only: cash, market clock, each ETF tradable + fractionable
python -m spare paper-order --dollars 5   # one $5 paper buy of VTI, then finds it again by its id
```

Test orders are tagged `spare-test-`, so the weekly job neither counts them nor flags them as unknown.

## Set up the Telegram bot

1. Make a bot with **@BotFather** and copy its token. Send your new bot any message.
2. `TELEGRAM_BOT_TOKEN=... python -m spare telegram-chat-id` prints your chat id.
3. Onboard on your computer: `TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python -m spare onboard`.
   The bot asks for your goals, your feelings about risk and your time horizon in your own words,
   then the multiplier, buy day, monthly cap and whether to allow single stocks. It saves
   `profile.toml` and `config.toml` in `state/` and creates your first proposal. Onboarding never
   switches off dry run.
4. Add a **`telegram`** environment with `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and
   `TELEGRAM_HMAC_KEY` (any random 32+ character secret, e.g. `openssl rand -hex 32`).

The hourly `bot` job sends your messages, offers proposals with **Approve / Skip**, and handles
`/status`, `/pause`, `/resume` and `/profile`. It only answers your chat id, and every button is
signed, so a forged tap does nothing. Unanswered proposals expire after 7 days as Skip.

## Add the Claude allocator

The allocator suggests your target mix from your three profile sentences. Claude proposes, code
decides: the answer must match a JSON schema (enforced by the API), pass every guardrail, and use
only symbols in the mix and numbers from its inputs. A failure is retried once with the errors; a
second failure changes nothing. A small change (at most 10 points among the same funds, once a
week) is applied on its own. Anything bigger becomes a Telegram proposal that waits for your
Approve, and is checked again before it's applied.

Claude sees only your profile sentences (wrapped as data, never as instructions), your current
weights, the allowlist and the limits. It never sees transactions, merchants, account ids or prices.

1. Get an API key from the Anthropic Console.
2. Add an **`ai`** environment with one secret, `ANTHROPIC_API_KEY`. The `brain` job runs on
   Sundays at 16:00 UTC, installs the `anthropic` SDK from the hash-pinned `requirements-ai.txt`,
   and holds no other key, so it can't reach your bank or broker.
3. Optional: onboard with `ANTHROPIC_API_KEY` set (after `pip install --require-hashes --no-deps -r
   requirements-ai.txt`) and your first proposal comes from the allocator too. Without it you get a
   plain starter mix.

Every call is logged in `state/telemetry.jsonl` with tokens, cost, latency, model and prompt
version. `ai_monthly_budget` in `config.toml` (default $2.00) stops the review for the rest of the
month once it would be passed; buying carries on. Expect about $0.05 a week on Claude Opus 5.5.

## The Sunday update

After the allocation review, the same Sunday job sends about six lines to Telegram:

> This week (Mon Feb 23 to Sun Mar 1): 23 purchases, $8.41 in round-ups (2x).
> Invested Monday: $8.41 across 5 funds. Pot now $0.40.
> Holdings: $412.10 across 5 funds. Shadow all-VTI portfolio: $409.55.
> Spending: dining $84.20, groceries $61.10, shopping $40.00.
> No changes this week.
> Next buy Monday.

Every number comes from code. Claude Haiku may reword it, but its version is used only if it contains
no number that isn't already in the plain one and drops no dollar amount; otherwise the plain version
goes. The **shadow portfolio** mirrors each real buy, same day and dollars, into VTI alone, so you can
see honestly whether the mix is doing better or worse than one index fund. Spending categories come
from a cache, then simple rules, then Claude Haiku for the rest. Set `send_merchants_to_ai = false` to
keep merchant names off the AI entirely. See [PRIVACY.md](PRIVACY.md).

## Run it on GitHub Actions

In **your private copy**:

1. **Settings → Environments → New environment `money`**, limited to the `main` branch. Add these secrets:
   `TELLER_ACCESS_TOKEN`, `TELLER_CERT_PEM`, `TELLER_KEY_PEM` (the contents of the two `.pem` files),
   `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY` (paper keys).
2. **Settings → Variables → `SPARE_ENABLED` = `true`**. Without it the workflow does nothing, so the
   public repo never runs.
3. Optional: put your `config.toml` on the `state` branch. Without one the job uses the safe defaults:
   dry run and paper.
4. Run **Actions → invest → Run workflow** once, then check it with `python -m spare status` and
   `python -m spare doctor` after `git worktree add state origin/state`.

The `invest` job runs every weekday at 15:00 UTC, collects round-ups, and buys once a week on your
buy day. It commits only the ledger, run status, outbox and market values (for the weekly update) to the `state` branch. Pause it any time
with `/pause` in Telegram (it writes `{"paused": true}` to `state/flags.json`).

## Use your own copy privately

This repository is the public source. To run it with your own accounts, create your **own
private copy** (GitHub's "Use this template" or a private clone), because the `state` branch will
hold your ledger and holdings.

## Roadmap

| Phase | What | Status |
|---|---|---|
| 1 | Money engine, ledger, guardrails, demo (no network) | done |
| 2 | Alpaca adapter (paper) | done |
| 3 | Teller (Chase) adapter and enrollment | done |
| 4 | `invest` workflow and state branch | done |
| 5 | Telegram bot and onboarding | done |
| 6 | AI allocator and model-agnostic LLM adapter | done |
| 7 | Weekly update and merchant categorizer | done |
| 8 | Evals: guardrail compliance, profile fit, stability, prompt injection, cost | |
| 9 | Two-week paper-trading run | |
| 10 | Public.com adapter, docs, v0.1.0 | |

Later: an opt-in sell mode (long-term lots only, approval for every sell, wash-sale blocking).

## Disclaimer

Not investment advice. You run it, for your own money, at your own risk. See
[`DISCLAIMER.md`](DISCLAIMER.md). Licensed under the [MIT License](LICENSE).
