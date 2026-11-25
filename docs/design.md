# Design

A short tour of how Spare Change is built and why. The full plan, threat model and decisions are in
[PLAN.md](PLAN.md); the safety research is in [REVIEW.md](REVIEW.md); individual decisions are in
[adr/](adr/).

## One rule

**Claude proposes, code decides.** The model suggests an allocation. Plain, tested code in
`spare/core` decides whether it's allowed, how much to buy, and places the order. Nothing the model
says can move money outside the guardrails.

## Three jobs, three sets of keys

```text
              +-------------------- state branch (JSON files) --------------------+
              |  ledger.jsonl  targets.json  proposals/  outbox/  flags.json  ...  |
              +-----^---------------------------^-----------------------^----------+
                    |                           |                       |
   invest (weekdays)                  brain (Sundays)            bot (hourly)
   environment: money                 environment: ai            environment: telegram
   bank + broker keys                 ANTHROPIC_API_KEY only     Telegram token + HMAC key
   stdlib only, spare/core            spare/brain                spare/bot
```

The jobs share nothing but files. A hijacked `brain` job (for example through a prompt injection)
holds no bank or broker key, so the worst it can do is write a proposal, and a proposal still has
to pass the guard and, if it's a big change, your signed Approve in Telegram.

## The money path

`spare/core/invest.py`, once per weekday:

1. **Reconcile** the broker's last 30 days of orders with the ledger: record our own orders the
   ledger missed, give back money for orders that died unfilled, flag any order we didn't place.
2. **Fetch** posted purchases from the bank and append one round-up per new transaction.
3. On the **buy day**, once a week, take the whole pot, clamped to the monthly cap. Under the
   broker's minimum ($1 Alpaca, $5 Public) it carries over.
4. **Validate** the approved targets again, check each symbol is tradable and fractionable, check cash.
5. **Split** the buy toward the targets using new money only (never sells), with no slice under the
   broker minimum, and validate the plan.
6. **Order** with deterministic ids derived from the round-ups being spent, so a re-run can't buy
   twice, and append each buy to the ledger.

Money is integer cents everywhere. The ledger is append-only; pot and holdings are derived from it.

## Brokers

| | Alpaca | Public |
|---|---|---|
| Paper account | yes | no (live only) |
| Minimum order | $1 | $5 |
| Market clock | yes | weekday check |
| Order id | our `spare-…` id | a UUID that encodes it |
| Tested against | paper account + mocks | mocks only |

Choosing Public counts as live: reads work in dry run, orders stay locked until the go-live
checklist passes. See [ADR 0003](adr/0003-public-adapter.md).

## The AI parts

- **Allocator** (`spare/brain/allocator.py`): Claude Opus 5.5 with a JSON schema enforced by the
  API. The profile is wrapped as data. Answers are checked for shape, guardrails, and explanations
  that name only symbols in the mix and numbers from the inputs; one retry with the errors, then
  nothing changes.
- **Weekly update** (`summary.py`): every number comes from code. Claude Haiku may reword it, but
  its draft is dropped if it adds a number or drops a dollar amount.
- **Categorizer** (`categorize.py`): cache, then rules, then Haiku with fixed category and merchant
  lists. Can be switched off so merchant names never leave GitHub.
- **Budget** (`telemetry.py`): every call is logged with tokens and cost; the month's budget stops
  the AI, never the buying.
- **Evals** (`evals.py`): 60 profiles and 50 injection attacks; an offline gate runs in CI.

## Setup

`python -m spare setup` writes a dry-run config and stores each secret only in the environment of
the job that reads it, through `gh` on stdin. See [ADR 0004](adr/0004-setup-wizard.md).

## What's deliberately missing in v1

Selling, tax-lot logic, margin, options, a hosted service, and any path where the model places an
order.
