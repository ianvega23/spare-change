# Spare Change

A free, open-source, self-hosted round-up investor. It rounds each card purchase up to the
next dollar, saves the spare change, and once a week it invests that week's total in fractional
shares of a diversified portfolio in your own brokerage account. An AI allocator suggests the mix from
the goals you describe in your own words, and plain code decides whether that suggestion is allowed.

It is built for developers who are comfortable with API keys and GitHub secrets. There is no
hosted service: you run it on GitHub Actions' free tier with your own Chase (via Teller),
Alpaca and Telegram accounts.

> **Status:** phases 1 to 3 of 10 (the money engine, the Alpaca paper adapter and the Teller bank link) are built and tested. Nothing here trades real money yet. The defaults are dry run
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
| 4 | `invest` workflow and state branch | |
| 5 | Telegram bot and onboarding | |
| 6 | AI allocator and model-agnostic LLM adapter | |
| 7 | Weekly update and merchant categorizer | |
| 8 | Evals: guardrail compliance, profile fit, stability, prompt injection, cost | |
| 9 | Two-week paper-trading run | |
| 10 | Public.com adapter, docs, v0.1.0 | |

Later: an opt-in sell mode (long-term lots only, approval for every sell, wash-sale blocking).

## Disclaimer

Not investment advice. You run it, for your own money, at your own risk. See
[`DISCLAIMER.md`](DISCLAIMER.md). Licensed under the [MIT License](LICENSE).
