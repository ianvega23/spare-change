# Review: architecture, safety, and broker choice (2026-11-24)

Written for plan v2 (`PLAN-v2.md`); still applies to the final plan v3 (`PLAN.md`). The original plan is `PLAN-v1.md`.

## 1. Verdict in one paragraph

The core design is right: a self-hosted, scheduled, idempotent script with a pure engine,
an append-only ledger, and paper + dry-run defaults. It is simpler and safer than any hosted
design, and it is the only shape that stays legal and free for everyone. **Alpaca is still the
best broker for this.** The changes worth making are (a) split secrets so the AI parts can never
touch the brokerage keys, (b) treat transaction text as untrusted input, (c) put the ledger on its
own branch, (d) add a second broker adapter (Public) so the project isn't tied to one company, and
(e) add an AI layer that *advises* while deterministic code *decides*.

## 2. Architecture review

| Question | Finding | Change |
|---|---|---|
| Weekly batch vs per-transaction | Right call. Alpaca's minimum notional order is $1, most single round-ups are below that, and weekly batching means one order, one fee-free trade, fewer API calls. | Keep. |
| GitHub Actions vs a server | Right for "free for anyone". No server to patch, free minutes cover it, logs and failure emails come free. | Keep. Local cron stays as the alternative. |
| Where secrets live | v1 put bank, broker and (soon) LLM keys in one job. If any step is compromised, all keys are exposed. | **Two jobs.** `invest` job holds Teller + Alpaca secrets and runs only stdlib code. `insights` job holds only the Anthropic key and reads the ledger. Secrets are scoped to a GitHub *Environment* limited to the `main` branch, so a PR branch can't read them. |
| Ledger committed to `main` | Works, but clutters history and means the job needs write access to the code branch. | Commit the ledger to an orphan `ledger` branch. Still counts as repo activity (keeps the cron alive). |
| Source of truth for "did we already buy?" | v1 already reconciles by `client_order_id`. Good. | Keep; make reconciliation the first step of every run, not a recovery path. |
| Market closed on Monday (holidays) | A notional `day` market order submitted when the market is closed queues for the next open. Harmless. | Document it; `status` shows queued orders. |
| Python stdlib only | Great for the money path (no supply-chain risk where keys live). | Keep for `spare/core`. The optional AI extra uses the official `anthropic` SDK, installed from a hash-pinned `requirements-ai.txt` (`pip install --require-hashes`), only in the `insights` job. |
| Plaid vs Teller | Teller is free for individuals and its auth (mTLS client cert + token) is stronger than an API key alone. Plaid's free Trial is capped at a small number of lifetime Items. | Keep Teller as default. Make the bank source an interface so Plaid can be dropped in. |

## 3. Safety of each dependency

**Alpaca (broker).** Alpaca Securities LLC is a FINRA and SIPC member and self-clearing.
SIPC covers up to $500,000 per customer (including $250,000 cash). Alpaca adds excess SIPC
through Lloyd's of London: up to $75M securities and $75M cash per customer, with a $250M
aggregate cap across all customers (Alpaca article dated 2026-09-08; an older 2023 FAQ page still
shows lower numbers). SIPC covers broker failure, not market losses. Cash swept to banks is not
SIPC-covered; Alpaca Crypto LLC is not SIPC or FINRA. Check the firm yourself on FINRA BrokerCheck (firm 288202).
*Key risk:* personal API keys are full trading keys with no read-only scope. A leaked key can place
trades but **cannot withdraw money** (the Trading API has no transfer endpoint; deposits and
withdrawals happen in the web dashboard). Mitigations: paper keys until go-live, Environment-scoped
secrets, a separate Alpaca account used only for round-ups, alerts on any order without the `spare-` prefix,
and rotating keys if a run log ever looks wrong.

**Teller (bank data).** Read-only by default; the access token alone is useless without the
client certificate and private key (mutual TLS). Free tier has undocumented rate limits (429s), which a
weekly job will never hit. *Risks:* Teller is a small company, so it could change its free tier or shut down;
Chase can force a re-link; and big banks have started charging data aggregators (JPMorgan did in 2025), which could
end up affecting free tiers. Mitigations: keep the bank source behind an interface (Plaid adapter as fallback),
request only the transactions endpoint, never enable Teller payments, store cert/key/token only as secrets.

**Plaid (fallback only).** Mature and widely audited, used by most fintech apps. It settled a privacy class
action over data collection in 2022, which is worth knowing. The free tier is too small to be the default.

**GitHub Actions.** Private repo via "Use this template" (not a fork). Pin actions to commit SHAs,
`permissions:` minimal per job, secrets in an Environment restricted to `main`, Actions disabled for
outside PRs. Never `echo` secrets; the log-redaction test stays.

**Anthropic API (new, optional).** Only the `insights` job has the key. It sees merchant names, amounts and
dates, never account numbers or broker keys. Users set a monthly spend limit in the Anthropic Console as a second guard on
top of the in-code budget. Cost estimate in `PLAN.md` §9.

**ntfy.sh (alerts).** Topics are public by name. Use a long random topic and send no amounts or merchant
names in push text, only "run ok" / "action needed".

**Regulation.** Unchanged and still correct: self-hosting only. Running it for other people's money
could make you a broker-dealer, investment adviser or money transmitter. An LLM that *recommends*
investments to others would raise the adviser question too, which is one more reason the AI layer
never picks securities and only suggests changes to the user's own settings.

## 4. Broker comparison

Needs: an official API for individuals, fractional or dollar-amount orders on ETFs, $0 commission,
and unattended auth (a weekly job can't log in by hand).

| Broker | Individual API | Fractional / dollar orders via API | Min order | Unattended auth | Verdict |
|---|---|---|---|---|---|
| **Alpaca** | Yes (built for it) | Yes, notional orders | $1 | API key + secret, no expiry | **Best fit.** Free paper trading makes testing safe. |
| **Public** | Yes (Individual API Program, personal use only) | Yes, `amount` orders | $5 during market hours | Secret key → access token | **Good second adapter.** $5 minimum means carrying round-ups over longer. Terms say the API may become paid later and Public can end access any time. No paper trading. |
| Schwab (Trader API) | Yes | Not via API; Stock Slices covers S&P 500 stocks, not ETFs like VTI | n/a | Refresh token expires every 7 days, needs a manual browser login | **No.** The weekly re-login breaks automation. |
| Interactive Brokers | Yes | IBKR says fractional is "not via API at this time" (FIX only) | n/a | Gateway needs periodic re-auth | **No.** |
| Tradier | Yes | No fractional shares | Whole shares | Token | **No.** |
| Robinhood / Fidelity / Vanguard | No official stock-trading API for individuals | n/a | n/a | n/a | **No.** |

**Recommendation:** Alpaca as the default, add a Public adapter in a later milestone. Two brokers behind
one `Broker` interface is also good engineering evidence for a portfolio.

## 5. Sources

- Alpaca asset protection: https://alpaca.markets/learn/customer-asset-protection-at-alpaca
- Alpaca insurance FAQ: https://alpaca.markets/support/is-my-account-insured-is-alpaca-legit
- Public API FAQ: https://public.com/api/docs/faq
- Public Individual API Program terms: https://public.com/documents/individual-api-program
- IBKR fractional via API: https://www.interactivebrokers.com/support/faq/content/84637087
- Schwab token lifetime (schwab-py docs): https://schwab-py.readthedocs.io/en/stable/auth.html
- Teller API docs: https://teller.io/docs/api
- Schwab fractional shares: https://www.schwab.com/fractional-shares

Not independently verified this round: Tradier's current fractional status and Teller's exact free-tier
connection limit. Check both before writing the README.
