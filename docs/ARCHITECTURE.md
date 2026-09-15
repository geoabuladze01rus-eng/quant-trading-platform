# Architecture

The platform has isolated crypto and Russian-market contours. Binance, Bybit and
OKX expose public/read-only market data. T-Invest exposes only sandbox/read
boundaries. `MARKET_SCOPE=mixed` combines dashboard visibility, not credentials,
balances, strategies, or routing.

There is one canonical Python package, `src/quant_trading_platform`, and one depth
simulator, `PaperExecutionEngine`. The durable `PersistentPaperService` composes
that engine with `SQLitePaperStore`; it does not introduce exchange execution or
import connector order methods.

## Durable paper command

```text
public REST books
  → NormalizedOrderBook validation (venue, symbol, market, time, depth)
  → CrossVenueSpreadMonitor
  → RiskEngine
  → read-only preview
  → Idempotency-Key reservation
  → SQLite BEGIN IMMEDIATE
       account + balance gates
       order lifecycle
       quote/base reservations
       depth-weighted paired fills and actual fees/slippage
       balance and position update
       structured audit transitions
       accounting reconciliation snapshot
       stored idempotent response
    COMMIT or full ROLLBACK
```

SQLite stores accounts, balances, orders, fills, positions, audit events,
idempotency records, and optional reconciliation snapshots. WAL, foreign keys,
busy timeout, stable identifiers, a schema version, and exact decimal JSON are
enabled. Initialization and account seeding are idempotent. A file path provides
restart recovery; test databases use isolated temporary paths or isolated shared
memory databases.

Partial execution matches both spread legs to the smaller available depth. Fees
apply only to executed notional. The remaining quote and base are reserved while
the order is `partially_filled` and are released on cancellation. This is a local
paired simulation, not a claim of cross-venue atomicity or settlement.

The database transaction is the invariant boundary: balances cannot go negative;
fills, balance changes, positions, audit, and the stored response commit together.
Failures roll the complete command back. A same-key replay reads its stored
response; a conflicting payload is rejected.

## Read and compatibility paths

Persistent APIs expose account, balances, orders, fills, positions, performance,
reconciliation, filtered audit, preview, create, and cancellation. GET endpoints
do not write. The earlier in-memory `/paper/orders/simulate` contract remains for
compatibility and is explicitly not the durable portfolio path.

## Residual review boundary

The canonical durable command still matches buy/sell quantities to common depth.
Immutable `PaperLegEvent` DTOs and the pure residual assessor represent potential later
independent outcomes without adding an execution engine. Duplicate event IDs count
once; conflicting payloads reject. A residual decision is `flat`,
`hedge_required`, or `halted`. A fresh public-book mark with source/time, a
configured notional cap and clean accounting are required before showing a
review-only, non-confirmable paper hedge proposal. No hedge submission is present.

GET `/paper/residual-exposure` derives observations from saved simulated fills and
read-only reconciliation. It does not poll, persist, execute or repair. Current
paired fills are flat; an accounting mismatch halts the observation. Independent
fills would need atomic integration into the existing balance/fill/audit ledger
before becoming a durable paper execution lifecycle.

Market quote snapshots and unchanged-signal deduplication remain in memory because
they are transient feed state. Durable trading/audit state does not rely on those
caches. Live execution is not implemented.
