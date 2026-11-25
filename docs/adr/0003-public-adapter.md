# ADR 0003: Public.com adapter: live only, $5 minimum, orders locked behind the go-live gate

**Status:** accepted (phase 10)

## Context

Public is the backup broker in the plan. Its Trading API (https://public.com/api/docs, read on
2026-11-25) supports dollar-amount (notional) market buys of fractional shares, which is all the
money path needs. Unlike Alpaca it has:

- **no paper or sandbox account**: every order is real money;
- a **$5 minimum** for fractional and dollar-amount orders (Alpaca's is $1);
- **no market-clock endpoint**;
- order lookup and order search limited to the **last 30 days**, search capped at 500 orders with no paging;
- **asynchronous placement**: the place-order call returns only an id, and the order may not be
  visible for a moment;
- a short-lived **access token** made from your secret key (5 to 1,440 minutes);
- a rate limit of 10 requests per second per account.

The docs show the order schema but no full JSON example for a dollar-amount buy, so the body is built
from the schema.

## Decision

- `broker = "public"` in `config.toml`, with one secret, `PUBLIC_SECRET_KEY`, in the `money`
  environment. `public_account_id` picks the account if you have more than one brokerage account.
- **Reads always work; orders don't.** The adapter refuses to place an order unless it was built with
  `allow_orders=True`, which `jobs.broker_from_env` passes only when `dry_run = false` and
  `state/go_live.json` records a passed checklist. Because Public can't do paper trading, choosing it
  counts as live everywhere (`jobs.wants_live`), and the two-week paper run happens on Alpaca first.
- **The minimum is the broker's, never lower than $1.** The adapter publishes `min_order_cents = 500`;
  `invest.run` passes `max($1, broker minimum)` to `engine.buy_amount`, `engine.split` and both guard
  checks, so a week under $5 carries over and no slice is under $5.
- **Order ids.** Public needs an RFC 4122 UUID and drops a resend with the same id. Our deterministic
  `spare-<24 hex>` id is mapped into a version-4-shaped UUID with a fixed prefix (`5ba7c0…`) and mapped
  back when reading, so reconcile still tells our orders from unknown ones.
- **Unclear failures.** An order is sent once. On a timeout or server error the adapter looks the
  order up by its id; if it isn't there, it resends the identical body once. A 4xx refusal (other than
  429) is never resent.
- **Market hours.** `market_open` is a weekday check. On a holiday a DAY order is rejected or expires
  unfilled, and reconcile returns the money to the pot.
- **Cash** is `cashOnlyBuyingPower`, and every order says `useMargin: false`.
- **Prices** come from the portfolio (`lastPrice` of a position). The shadow portfolio needs VTI's
  price, so with Public it only fills in once you hold VTI; Alpaca's market data isn't used.
- Tested with a scripted transport only (`tests/test_broker_public.py`).

## Consequences

- Nobody can try Public without real money. Before switching, run the paper period on Alpaca, then
  start Public with `dry_run = true` for a week and compare `spare status` with the Public app.
- If Public adds a sandbox or a market clock, both are a small change in `broker_public.py`.
- Reconcile looks back 30 days, which matches Public's order history limit.
