# ADR 0001: Use Alpaca's official skills repo as the API reference, pinned

**Status:** accepted (phase 2)

## Context

The broker adapter talks to Alpaca's Trading API with the Python standard library only, so there's
no SDK to lean on for endpoints, field names and error behaviour. Community Alpaca "skills" vary in
quality and aren't reviewed, and copying from them into the money path is a supply-chain risk.

## Decision

Use Alpaca's own repository, `github.com/alpacahq/alpaca-skills` (Apache-2.0), as the written
reference, pinned to:

`0e7c35163ae462730de1f4a0bfafceac82b7d59b` (2026-11-06)

Nothing from it is vendored or executed. It was read for these facts, which `spare/core/broker_alpaca.py`
follows and `tests/test_broker_alpaca.py` encodes:

| Topic | What the reference says | What the adapter does |
|---|---|---|
| Endpoints | `GET /v2/account`, `GET /v2/clock`, `GET /v2/assets/{symbol}`, `POST /v2/orders`, `GET /v2/orders`, `GET /v2/orders:by_client_order_id` | Uses exactly these |
| Notional orders | Equities need `type=market`, `time_in_force=day`; notional takes 2 decimals; never with `qty` | Sends `notional` as a 2-decimal string built from integer cents |
| Fractional | Check the asset's `fractionable` flag | Guard rejects any target that isn't tradable, active and fractionable |
| Idempotency | Set your own `client_order_id` (max 128 chars); after an unclear failure, look the order up by it before retrying | Deterministic `spare-…` ids; POST is never retried; timeouts and 5xx trigger a lookup |
| Errors | 401 stop, don't retry; 403 on orders = insufficient buying power; 422 = bad input (also a duplicate id); 429 back off and honour the reset | 401/403 raise at once; 422 naming `client_order_id` raises `DuplicateOrder`; 429/5xx/network errors on GETs retry with capped backoff |
| Precision | Money is strings on the wire; parse to decimal, never float; round deliberately | Amounts become integer cents rounded down; quantities and prices are stored as the strings Alpaca sent |
| Reconciliation | Treat the broker as the source of truth; re-pull a window and converge | Each run lists 30 days of orders: records our missed orders, voids ours that ended unfilled, alerts once on any order we didn't place |

## Consequences

- Updating the pin is a deliberate change: re-read the diff of the reference, update this table and the tests.
- The adapter refuses live trading unless the caller passes `allow_live=True`, which only the invest job will
  do after the paper go-live checklist (phase 9) passes.
