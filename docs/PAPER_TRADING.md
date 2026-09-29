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

## Crypto paper robot

The disabled-by-default robot evaluates exactly `BTC/USDT`, `ETH/USDT`, and
`LTC/USDT`. Each venue/pair has an independent health state. Missing, stale,
future-dated, incomplete, or failed data blocks that pair without authorizing a
trade from a cached book. Public venue rules supply price ticks, quantity steps,
minimum quantities, and minimum notionals.

A local paper spread is created only when its expected net edge remains positive
and above the configured minimum after fees, configured slippage, and depth
slippage. Rejected decisions are written to audit with a stable reason code.
Account reconciliation mismatch, an open partial order, the daily loss limit, an
insufficient prefunded leg, or an unsafe trading configuration blocks new orders.

When `CRYPTO_PAPER_DIRECTIONAL_ENABLED=true`, the same robot may also open and
close long-only local positions. The model has fixed 12/48 observation windows;
it does not tune parameters from the current sample. Entry requires enough price
history, positive expected edge after a 0.10% fee and 0.05% slippage estimate per
side, and a walk-forward report with the configured minimum closed trades,
positive average net result and bounded drawdown. Exit uses the same public depth.
The persistent account keeps a separate strategy inventory and cost basis, so
seed holdings cannot be sold or counted as robot P&L. No result is guaranteed.

The robot is part of the FastAPI lifecycle; do not start a second worker against
the same SQLite account. For a separate bounded test, use a separate database:

```bash
PAPER_DATABASE_PATH=/tmp/quant-crypto-paper-trial.sqlite3 \
CRYPTO_PAPER_ROBOT_ENABLED=true \
CRYPTO_PAPER_DIRECTIONAL_ENABLED=true \
TRADING_MODE=paper LIVE_TRADING_ENABLED=false LIVE_ORDER_ACCEPTANCE_GATE=false \
uvicorn quant_trading_platform.api.app:app --host 127.0.0.1 --port 8000
```

`GET /crypto/paper-robot` returns a decision state for every configured symbol.
`GET /paper/performance` reports realized and unrealized P&L separately. Unknown
marks or cost basis remain `null`; the dashboard must not label cash movement as
profit.

The older `/paper/orders/simulate` endpoint remains as a volatile compatibility
simulation. It does not mutate the persistent virtual portfolio.

## Alpha limitations

Initial non-USDT holdings may receive a public read-only midpoint valuation, but
the system never invents their historical cost basis. Therefore total-account
unrealized P&L remains `null` while
`unpriced_pnl_assets` identifies the assets whose historical cost is unknown.
Strategy unrealized P&L is reported separately from its own durable cost basis.
Reported performance is hypothetical, not independently verified and never a promise
of profit.

The residual view is observation-only. It replays saved simulated leg events and
shows `flat` for currently paired fills. An accounting mismatch produces `halted`
and no paper hedge proposal. A pure immutable DTO can model future independent
partial legs with fresh mark/cap evidence, but it is not yet an accounting command
and never executes a hedge. The next milestone is atomic integration of such leg
events into the existing balance/fill/audit ledger, followed by long-running paper
validation; no parallel engine or uncontrolled background hedge is planned.
