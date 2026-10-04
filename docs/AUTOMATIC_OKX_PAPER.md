# Automatic OKX spot paper operations

## Diagnosis on 2026-10-04

GitHub main was `4e9bbe6866476f863e758a5eb9b6cdc85b0a4213`. It starts market-data
producers and records opportunity/risk audit events. Persistent execution is
called by POST `/paper/orders`, not by a producer. It cannot autonomously create
spot positions. A passing CI or an opportunity event is not evidence of a fill.

PR #20 adds three market feeds; #21, stacked on it, adds isolated manual OKX spot
accounting; #22 adds research signals and historical replay. These PRs were open
drafts, not merged into main. PR #18/#19 contain another directional design with
documented unresolved admission/accounting defects. This repair integrates
#20/#21/#22 and implements the missing producer-to-durable-fill path without
adopting the unresolved #18/#19 ledger.

Newest pre-existing CI runs #36680543747 and #36680381971 were successful for
#22 SHA `f4e0c78780d8c8c39da4f1c79d3e0a6367d29856`. These are historical checks,
not verification of this repair.

The prior session's local database is no longer available after workspace
maintenance. Its last observed state on September 30 was an unchanged spread
account with zero orders/fills. No current user deployment URL, broker sandbox
session or execution volume was available for this audit. Current owner trades
and owner balance therefore remain unobserved.

## Strategy and accounting

- Fixed daily trend: entry on a completed 20-day breakout above SMA100; exit below
  SMA100 or the preceding 10-day low. No short selling or pyramiding.
- Execute against the next available current book after the completed signal,
  not at the historical candle price. This forward model differs from the
  next-day-open assumption of the historical replay.
- Revalidate entry against the current book: bid must remain above the signal's
  SMA100, and ask must be within 2% of the completed signal close by default
  (`OKX_SPOT_AUTO_MAX_ENTRY_DEVIATION_PCT`). Otherwise defer without a fill.
- One entry per symbol/completed candle. The strategy cursor commits with the
  order, fill, balances, average cost, positions, reconciliation and audit.
- Startup reconciliation and each command reconstruct cash in chronological
  commit order. This repairs non-associative finite-precision Decimal replay.
- A quantity-based exit can be split into capped chunks. Every chunk has a
  durable inventory-derived key and the final chunk closes the exact remainder.
- The spread account remains distinct; extending a legacy universe adds zero
  LTC rather than silently crediting additional virtual funds.
- Rejected commands roll back their pending idempotency reservation. Unexpected
  storage errors roll back the entire cycle and halt the automatic account.
- Manual spot commands return 409 while this account is strategy-managed.
- Reconciliation also verifies fill identity/price, reconstructed average cost,
  and the presence and cost of each funded position. Fractional-quantity replay
  uses the same Decimal operation order as execution.
- Relative strength is displayed for research; it is not an execution strategy.
- No news filter or profitability acceptance is claimed. This is an experimental
  paper forward test. Trend signals do not provide a known expected return.

## Default bounds

Virtual account starts with 10,000 USDT and zero base inventory. This is not a
100,000 RUB deposit and is not an OKX exchange-hosted demo account.

Each order has a 100 USDT notional cap. Acquisition cost per asset is bounded to
5% of initial USDT; total acquisition cost to 10%. Automatic entry also checks
current marked portfolio exposure against the total cap. No more than one
position per pair. The entry spread cap is 0.20%, fee is 0.10% of notional and
additional slippage cash cost is 0.05%; fills walk actual bid/ask depth, so spread
is already included in execution prices.

The default daily equity-loss gate is 2%, including marked open inventory.
Baseline uses the previous observed equity at UTC rollover and the initial seed
on first start. The baseline and daily halt survive restart. After an outage it
conservatively includes the gap since the last observation; it is not a verified
midnight close. Hitting the gate stops purchases and still permits funded trend
exits. It does not guarantee a maximum loss or force liquidation.

All three books must pass identity, depth, timestamp and freshness checks (default
1,000 ms) for a cycle. All three completed candle series must be continuous and
share yesterday's UTC date. Missing or stale data blocks execution. There is no
fallback from an errored source to a last-good book.

## Launch and inspect

Docker Compose binds localhost and enables the local paper runner:

```sh
docker compose up --build -d
curl http://127.0.0.1:8000/paper/okx/robot
curl http://127.0.0.1:8000/paper/okx/account
curl http://127.0.0.1:8000/paper/okx/history
curl 'http://127.0.0.1:8000/audit?event_type=auto_paper_cycle&limit=20'
```

Direct Python launch (use your existing persistent database path):

```sh
OKX_SPOT_AUTO_ENABLED=true python -m uvicorn quant_trading_platform.api.app:app \
  --host 127.0.0.1 --port 8000
```

Startup fails before opening the database if automatic execution is enabled
without public crypto feeds for all three BTC/ETH/LTC pairs, or with the Russian
stocks scope. An enabled but incapable configuration must not appear healthy.

The status response reports enabled, worker_running, heartbeat_age_ms,
valuation_age_ms, effective risk/cost configuration, each pair's action/reason,
and order/fill counts. A running
worker with zero fills can correctly mean no breakout, an existing position,
an already processed daily signal, a spread/depth/risk rejection, or missing data.
Read the reason, not just the heartbeat. Monitor routes are read-only.

The dashboard has a separate automatic OKX paper panel and refreshes every five
seconds. It does not confuse the spread account's balance with the spot account.
The panel provides paper-only pause/resume. Control commands require a trusted
origin and a durable Idempotency-Key. For a localhost deployment:

```sh
curl -X POST http://127.0.0.1:8000/paper/okx/robot/control \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: operator-pause-001' \
  -d '{"action":"pause"}'
```

Use a new key and `{"action":"resume"}` to resume. Pause survives restart and is
checked again inside the execution transaction after market-data collection.
It stops subsequent cycles, not a transaction already in progress, and does not
liquidate positions. Resume requires healthy reconciliation and does not reset
the signal cursor, daily-loss halt or a worker failure.

Journal only records changed decisions; heartbeat and changing cost estimates
alone do not generate a new audit event every poll. Stop the local process to
stop execution; set
`OKX_SPOT_AUTO_ENABLED=false` in your launch configuration to disable subsequent
starts. Docker Compose has an explicit true setting that must be changed there.

## Verification boundary

Regression tests run actual durable SQLite transactions with synthetic public
market fixtures: parser → signal → background worker → BUY → next-day SELL,
restart, two concurrent workers, stale/unavailable data, wide spread, depth
failure, daily unrealized loss, overnight gap, storage fault rollback, exact
partial-exit recovery and legacy-account migration. Fixture fills and losses
prove execution/accounting behavior, not a trading advantage.

Hardening regressions additionally cover current-price gaps, durable pause and
resume, pause while collecting inputs, corrupt ledger/position detection,
fractional inventory liquidation, audit de-duplication, control-route origin and
idempotency enforcement, and incompatible startup configurations. On October 4
the local suite passed all 342 tests, Ruff and mypy; the frontend production build
also passed. See PR #23 for the exact published revision and hosted CI results.

Direct public OKX access timed out in this execution environment. No real-market
forward return, owner balance change, multi-year profitability or unattended
server uptime is asserted. Live transport remains unimplemented and T-Invest
remains isolated. Local Docker execution requires Docker; hosted CI includes
its existing Docker build/health smoke gate.

An isolated 18-second FastAPI probe was also executed against a newly created
temporary paper database. `/health` returned paper/locked; `/paper/okx/robot`
reported enabled=true, worker_running=true and `completed_candles_unavailable`;
orders/fills=0, initial equity=10,000 USDT, reconciliation=ok. The probe was stopped
after inspection. This is a new test account, not the owner's account. It confirms
that upstream unavailability appears as a durable reason rather than fabricated
trades. A local Docker runtime was unavailable.
