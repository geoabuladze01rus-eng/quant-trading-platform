# Persistent Paper Trading Alpha

Paper Alpha is a funded local simulation over public Binance, Bybit, and OKX depth.
It never submits a venue order. T-Invest is separate and remains sandbox/read-only.

## Account and lifecycle

On first startup, an idempotent seed creates a virtual account with USDT, BTC, and
ETH balances. Configure amounts with `PAPER_INITIAL_USDT`, `PAPER_INITIAL_BTC`, and
`PAPER_INITIAL_ETH`. Existing balances are never overwritten on restart.

Orders move through `created`, `accepted`, `partially_filled`, `filled`, `cancelled`,
`rejected`, or `failed`. The service requires both virtual quote balance for the
buy and base inventory for the sell. It reserves the requested resources, consumes
normalized book depth, charges fees on filled notional, applies the conservative
slippage cost, updates balances/positions/P&L, and audits each transition.

If common buy/sell depth fills only part of the request, the order is
`partially_filled`; the unfilled resources remain reserved until cancellation.
This deliberately avoids unpaired simulated exposure but does not model real venue
atomicity, independent fills, or settlement.

## Exact arithmetic and reconciliation

Money, quantity, fees, slippage, balances, and P&L use `Decimal`. SQLite stores
canonical decimal strings and rejects floats. The accounting identity is rebuilt
from initial balances plus fills, fees, and slippage. Reconciliation also checks:

- reserved balances against open orders;
- positions against balances/fills;
- order totals against fill totals;
- fee rate against executed notional;
- orphan or negative fills and balances;
- missing fills and impossible lifecycle states.

Issues contain `reason_code`, `human_reason`, affected entity, timestamp,
correlation ID, and severity. GET `/paper/reconciliation` is side-effect free;
internal callers may explicitly persist a snapshot.

## Idempotency and restart recovery

Use a unique `Idempotency-Key` for each create or cancel intent. SQLite reserves
the key under `BEGIN IMMEDIATE`; order state, audit, and the original response are
committed together. The same payload returns that response after process restart.
Another payload with the same key returns `duplicate_idempotency_key` and creates
nothing. Concurrent distinct commands serialize at the database boundary and
cannot overspend. On every application startup, a persistent reconciliation snapshot
is created. If it detects a mismatch, the account becomes `halted` and new paper
orders are blocked until the ledger is repaired and a clean restart passes reconciliation.
Cancellation remains available to release an existing reservation.

The default database is `data/paper_alpha.sqlite3`. For backup, stop the application
and copy the SQLite database plus any WAL files as one consistent set, or use
SQLite's online backup API. This alpha does not yet provide automated migrations,
retention, encryption, multi-host coordination, or restore commands.

## API example

```bash
curl -X POST http://127.0.0.1:8000/paper/orders/preview \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTC/USDT","buy_venue":"binance","sell_venue":"okx","notional_usdt":"10"}'

curl -X POST http://127.0.0.1:8000/paper/orders \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 870e1c63-e4af-4db6-baea-439194692dc8' \
  -d '{"symbol":"BTC/USDT","buy_venue":"binance","sell_venue":"okx","notional_usdt":"10"}'

curl http://127.0.0.1:8000/paper/account
curl http://127.0.0.1:8000/paper/balances
curl http://127.0.0.1:8000/paper/orders
curl http://127.0.0.1:8000/paper/fills
curl http://127.0.0.1:8000/paper/positions
curl http://127.0.0.1:8000/paper/performance
curl http://127.0.0.1:8000/paper/reconciliation
curl http://127.0.0.1:8000/paper/residual-exposure
curl 'http://127.0.0.1:8000/audit?limit=50&offset=0&event_type=paper_order_filled'
```

The older `/paper/orders/simulate` endpoint remains as a volatile compatibility
simulation. It does not mutate the persistent virtual portfolio.

## Alpha limitations

Initial non-USDT holdings have no mark until a validated paper fill supplies one.
A fill may establish a valuation mark, but it never invents a cost basis for
pre-funded inventory. Therefore unrealized P&L remains `null` while
`unpriced_pnl_assets` identifies the assets whose historical cost is unknown.
Reported performance is hypothetical, not independently verified and never a promise
of profit.

The residual view is observation-only. It replays saved simulated leg events and
shows `flat` for currently paired fills. An accounting mismatch produces `halted`
and no paper hedge proposal. A pure immutable DTO can model future independent
partial legs with fresh mark/cap evidence, but it is not yet an accounting command
and never executes a hedge. The next milestone is atomic integration of such leg
events into the existing balance/fill/audit ledger, followed by long-running paper
validation; no parallel engine or uncontrolled background hedge is planned.
