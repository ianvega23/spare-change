# PLAN: Spare Change, a free self-hosted round-up investor

> Implementation plan for Claude Code. Put this file at the repo root as `PLAN.md`
> (and point `CLAUDE.md` at it). Work through the milestones in order. Each one ends
> with green tests and a commit. Don't start the next milestone until the current one passes.

## 1. What we're building

A small open-source tool that anyone can copy from GitHub and run **for themselves**:

1. Once a week, read posted purchases from the user's bank (Teller API).
2. Round each purchase up to the next dollar ($4.25 → $0.75).
3. Buy that total as a fractional share of one ETF in the user's own Alpaca account.
4. Record what it did in a ledger so nothing is ever counted or bought twice.

**Cost to the user: $0.** Teller's developer tier is free (up to 100 live bank connections),
Alpaca has no commissions or minimums, and GitHub Actions is free for this workload.
Acorns charges $48 to $144 a year for the same thing.

**What "free for everyone" means:** every user runs their own copy with their own API keys
and their own accounts. There is no shared server and nobody else's money ever passes
through the project. That is what keeps it simple and legal (see section 9).

**Design rules (in priority order):** never invest money twice, never invest more than the
user's caps, fail loudly, stay simple. Python 3.11+, standard library only (no `pip install`).

## 2. Money flow (the one manual step)

Alpaca's API cannot pull money from a bank, so the bank → brokerage transfer is not automated.
The user sets up a **recurring monthly ACH deposit** from Chase into Alpaca once, in Alpaca's
web dashboard (e.g. $25/month). That cash sits as a buffer, and the weekly job invests the
round-ups out of it. If the buffer runs low, the job stops buying and alerts the user.

```
Chase checking ──(Teller API, read-only)──▶ weekly job ──(Alpaca API, buy order)──▶ Alpaca
      │                                                                              ▲
      └──────────────(monthly recurring deposit, set up once by the user)────────────┘
```

## 3. Architecture

Four small pieces with one job each. The engine is pure logic and never touches the network.

| Piece | File | Job |
|---|---|---|
| Bank source | `spare/bank_teller.py` | Fetch posted transactions for the last 14 days (mTLS with the user's Teller cert). |
| Engine | `spare/engine.py` | Filter purchases, compute round-ups, apply multiplier and caps, decide the order. Pure functions. |
| Broker | `spare/broker_alpaca.py` | Check buying power, place a notional market order with a deterministic `client_order_id`. |
| Ledger | `spare/ledger.py` | Append-only JSON Lines file: every transaction id seen, every order placed. The source of truth for dedup. |

Plus `spare/fake.py` (fake bank + fake broker for tests and demo mode), `spare/config.py`
(load + validate settings), `spare/notify.py` (alerts), and `spare/cli.py` (entry point).

**Weekly run, step by step**
1. Load and validate config. Refuse to run on any invalid value.
2. If `ENABLED` is not `true`, log "disabled" and exit 0 (the kill switch).
3. Fetch transactions from the last 14 days (the overlap catches late-posting charges).
4. Drop anything pending, any credit (refunds, deposits, transfers in), and any id already in the ledger.
5. Round up each purchase, multiply, add any carried-over balance below $1 from last week.
6. Clamp to `WEEKLY_CAP`. If the total is under $1 (Alpaca's minimum order), carry it over and stop.
7. Build `client_order_id = "spare-" + sha256(sorted tx ids)[:20]`. Same inputs, same id, so a retried
   run can never place a second order (Alpaca rejects duplicate client order ids).
8. Check Alpaca buying power. If it's short, alert "top up your Alpaca account" and carry the amount over.
9. In dry-run mode: print the plan and stop. Otherwise place the order.
10. Write the ledger (tx ids + order id + amount), commit it back to the repo, print a summary.

## 4. Repo layout

```
spare-change/
├── README.md                 # 10-minute setup guide for non-experts (section 6)
├── PLAN.md                   # this file
├── CLAUDE.md                 # "follow PLAN.md; run tests before every commit"
├── LICENSE                   # MIT
├── DISCLAIMER.md             # not financial advice, self-host only, use at your own risk
├── config.example.toml       # every setting with a comment and a safe default
├── spare/
│   ├── __init__.py
│   ├── __main__.py           # python -m spare ...
│   ├── cli.py                # run | enroll | status | doctor
│   ├── config.py
│   ├── engine.py
│   ├── ledger.py
│   ├── bank_teller.py
│   ├── broker_alpaca.py
│   ├── notify.py
│   ├── fake.py
│   └── enroll.html           # Teller Connect page served locally during setup
├── data/
│   └── ledger.jsonl          # created on first real run; committed by the workflow
├── tests/
│   ├── test_engine.py
│   ├── test_ledger.py
│   ├── test_config.py
│   ├── test_broker_alpaca.py # HTTP mocked, no network
│   ├── test_bank_teller.py   # recorded fixture responses, no network
│   └── test_end_to_end.py    # fake bank + fake broker, full weekly runs
└── .github/workflows/
    ├── weekly.yml            # the scheduled job
    └── ci.yml                # tests on every push / PR
```

Reuse the existing prototype (`roundup-prototype/`): `round_up_cents`, the integer-cents money
handling, the dedup test and the fake Chase data carry over almost unchanged. Replace the Plaid
source with Teller and replace `state.json` with the append-only ledger.

## 5. Configuration

Secrets go in **GitHub Actions secrets** (or a local `.env` that is git-ignored). Everything
else goes in `config.toml` (parsed with stdlib `tomllib`) or Actions variables, which override it.

| Setting | Kind | Default | Notes |
|---|---|---|---|
| `TELLER_ACCESS_TOKEN` | secret | none | From `python -m spare enroll`. |
| `TELLER_CERT_PEM`, `TELLER_KEY_PEM` | secret | none | Teller client certificate + key (mTLS). |
| `TELLER_ACCOUNT_ID` | variable | none | Which account to read; `enroll` prints it. |
| `ALPACA_KEY_ID`, `ALPACA_SECRET` | secret | none | |
| `ALPACA_ENV` | variable | `paper` | `live` must be typed explicitly. |
| `DRY_RUN` | variable | `true` | Must be set to `false` to place any order. |
| `ENABLED` | variable | `true` | Kill switch. |
| `SYMBOL` | variable | `VTI` | Placeholder, not advice. Validated against Alpaca: must be tradable and fractionable. |
| `MULTIPLIER` | variable | `1` | 1 to 10. |
| `WEEKLY_CAP` | variable | `25.00` | Hard max per weekly order. |
| `MONTHLY_CAP` | variable | `100.00` | Hard max per calendar month, read from the ledger. |
| `NTFY_TOPIC` | variable | empty | Optional push alerts via ntfy.sh (free). |

Live trading requires **all three**: `ALPACA_ENV=live`, `DRY_RUN=false`, and live keys. Anything
less places nothing real.

## 6. Setup for a non-expert (goes in README.md)

Target: about 15 minutes, no terminal skills beyond copy-paste.

1. **Copy the repo.** Click "Use this template" → create a **private** repository.
   (Private matters: the ledger lists your transaction ids and amounts. A fork of a public repo
   can't be private, so use the template button, not Fork.)
2. **Alpaca.** Sign up, start with a **paper** account, and copy the API key and secret.
   Later, open the live account and set up a monthly recurring deposit from your bank.
3. **Teller.** Create a free developer account at teller.io, create an application, download
   the certificate and private key, and note the application id.
4. **Link your bank (one time, on your computer).** Run `python -m spare enroll --app-id <id>`.
   It opens a local page with Teller Connect; log into Chase there. The command prints your
   access token and account list. No data leaves your machine except to Teller.
5. **Add secrets** in GitHub → Settings → Secrets and variables → Actions. The README shows
   each name exactly with screenshots.
6. **Test it.** Actions tab → "Weekly round-up" → Run workflow. It runs in dry-run and paper
   mode and shows what it *would* buy. Run `python -m spare doctor` locally to check keys and settings.
7. **Go live when ready.** Set `DRY_RUN=false` (paper orders). After a few good weeks, switch
   to live keys and `ALPACA_ENV=live`.

**Running locally instead of GitHub Actions:** same code, `cp config.example.toml config.toml`,
fill `.env`, and add a cron line: `0 15 * * 1 cd ~/spare-change && python3 -m spare run`.

## 7. Scheduling (`.github/workflows/weekly.yml`)

- `schedule: cron: "0 15 * * 1"` (Monday 15:00 UTC, about 10 AM Eastern, market open) plus `workflow_dispatch` for manual runs.
- `concurrency: { group: weekly, cancel-in-progress: false }` so two runs never overlap.
- `permissions: contents: write` only, so the job can commit `data/ledger.jsonl`.
- Steps: checkout → setup-python 3.12 → `python -m spare run` → commit ledger if it changed.
- Pin actions to full commit SHAs. No third-party actions beyond `actions/checkout` and `actions/setup-python`.
- GitHub cron can run late or skip a run. That's fine: the 14-day lookback plus the ledger catches up next time.
- GitHub turns off schedules after 60 days without repo activity. The weekly ledger commit keeps it active; `doctor` warns if the last run is old.

## 8. Reliability safeguards

| Risk | Safeguard |
|---|---|
| Double-counting a transaction | Ledger of processed ids; engine skips any id already there. |
| Double order from a retry or overlapping run | Deterministic `client_order_id`; workflow concurrency group; ledger is written only after the order succeeds, and on the next run an existing order with that id is looked up and recorded instead of re-placed. |
| Order placed but ledger commit failed | Same lookup: `run` first lists recent Alpaca orders with the `spare-` prefix and reconciles them into the ledger. |
| Late-posting charges missed | 14-day lookback window; dedup by id makes the overlap harmless. |
| Bad data (huge or odd amounts) | Round-up is 0 to 99 cents by definition; multiplier capped at 10; weekly and monthly caps clamp the total. |
| Accidental real money | Defaults are `paper` + `DRY_RUN=true`; live needs three explicit settings. Kill switch `ENABLED`. |
| Not enough cash in Alpaca | Check buying power first; alert and carry over instead of failing the order. |
| Bank connection expires (Chase may ask to re-link) | Teller auth errors map to a clear alert: "Re-run `spare enroll`". |
| Silent failure | Any error exits non-zero. GitHub emails the repo owner on failed workflows for free; optional ntfy push. Weekly summary is printed to the Actions log. |
| Leaked secrets | Secrets only from env; never logged (log redaction test); `.env` and `config.toml` git-ignored. |
| Money rounding bugs | All money is integer cents end to end; dollars appear only at the API edge as a 2-decimal string. |

**Logging:** one plain line per event (`fetched 23 txns`, `skipped 4 seen`, `round-up $3.41`,
`order spare-ab12… accepted`). No account numbers, merchant names truncated.

## 9. Legal and compliance (brief, keep in DISCLAIMER.md)

- **Self-host only.** The project is software, not a service. Don't run one instance that
  manages other people's money or keys; that could make the operator a broker-dealer,
  investment adviser, or money transmitter. The README says this plainly.
- **Not financial advice.** The default symbol is a placeholder. Users pick their own investment.
- **Provider terms.** Users accept Teller's and Alpaca's terms themselves. Teller's free tier is
  a developer tier meant for individual use; check its limits before sharing beyond yourself.
- **Taxes.** Every buy is a taxable-account purchase. Alpaca issues the 1099; the ledger helps.
- **License:** MIT with the standard "no warranty" clause.

## 10. Testing

- `python -m unittest discover -s tests` runs everything, offline, in under a few seconds. CI runs it on every push.
- **Engine:** round-up edge cases ($5.00 → 0, $10.01 → 0.99, refunds → 0), multiplier, caps, carry-over under $1, monthly cap across weeks.
- **Dedup:** run the same week twice → one order; overlapping windows → no double count.
- **Idempotency:** simulate "order placed, ledger not saved" → next run reconciles, no second order.
- **Teller:** parse recorded fixture JSON (posted vs pending, debit vs credit sign). Verify the sign convention against Teller's docs before writing the parser.
- **Alpaca:** mock HTTP; check request body, duplicate `client_order_id` handling, insufficient buying power, 4xx/5xx retries (max 3, backoff, only for safe/idempotent calls).
- **Config:** invalid values refuse to run; live mode needs all three switches.
- **End to end:** fake bank + fake broker across 8 simulated weeks; invested + pending equals total round-ups.
- **Manual:** before going live, one week of paper trading with real Teller data.

## 11. Milestones for Claude Code

Each milestone: write code + tests, run the full suite, commit. Keep each one a separate PR if working in a repo.

1. **Skeleton.** Repo layout, MIT license, `CLAUDE.md`, `config.example.toml`, `ci.yml`. Port the prototype engine and tests into `spare/`. *Done when:* CI is green.
2. **Engine + ledger.** Caps, multiplier, carry-over, append-only ledger, deterministic order id. Fake bank and broker. `python -m spare run --demo` works with no keys. *Done when:* engine, ledger and end-to-end tests pass.
3. **Config + safety switches.** Validation, dry-run default, paper default, kill switch, secret redaction. *Done when:* config tests pass and a misconfigured run exits non-zero with a clear message.
4. **Alpaca adapter.** Paper and live base URLs, buying power check, notional order, reconcile by `client_order_id`, retries. *Done when:* mocked tests pass and one manual paper order succeeds.
5. **Teller adapter + enroll.** mTLS client, transaction parsing, `spare enroll` local page with Teller Connect. *Done when:* fixture tests pass and enroll returns a token against Teller's sandbox.
6. **Scheduling + alerts.** `weekly.yml`, ledger commit step, ntfy alerts, `spare status` and `spare doctor`. *Done when:* a manual workflow run in dry-run mode succeeds and commits nothing; a forced failure sends an alert.
7. **Docs + first release.** Setup guide with screenshots, DISCLAIMER, troubleshooting section, tag `v0.1.0`. *Done when:* someone follows the README from scratch to a successful dry run.

## 12. Out of scope for v1

Multiple accounts or banks, portfolios of several ETFs, a web dashboard, automated bank
transfers, round-ups from credit cards (easy to add later: same Teller API, another account id),
and any hosted version for other people.
