# Safety gates

Live trading is locked by default. Every connector `place_order` calls the central
gate and rejects unless `LIVE_TRADING_ENABLED=true`, `TRADING_MODE=live`, the
acceptance gate is true, and T-Invest is not sandboxed. Even then, real execution
is absent and raises `NotImplementedError`. There are no withdrawal, transfer, or
private authenticated venue endpoints.

Application startup rechecks the safe configuration. Public crypto adapters use
keyless endpoints only. T-Invest remains sandbox/read-only. Secret fields are
excluded from settings representations and API responses; raw payloads and unknown
audit fields are rejected. `.env` and SQLite runtime files are ignored by Git and
the Docker build context.

## Paper command boundary

All mutable routes are under `/paper/`. A caller supplies only symbol, buy venue,
sell venue, and virtual notional. Prices, books, cost assumptions, timestamps,
mode, risk approval, and balances are server-owned. Local Vite origins are allowed;
CORS is not authentication, so this alpha must not be exposed publicly.

Create and cancel require `Idempotency-Key` (maximum 128 characters). The key and
canonical request hash are persisted in the same transaction as the result.
Duplicate payloads replay; conflicting payloads reject. Unknown command outcomes
must be resolved by reading the ledger, not by inventing a new key and retrying.

Before a fill, the engine rejects:

- live mode or an enabled live flag;
- market, venue, or symbol mismatch;
- missing, future, stale, errored, or invalid books;
- non-finite or non-positive values;
- notional/risk limits and insufficient net edge;
- insufficient depth;
- insufficient available virtual USDT or base inventory.

Balances cannot become negative. The transaction atomically persists order,
reservations, fills, balances, positions, audit, reconciliation snapshot, and the
idempotent response. Any write failure rolls everything back. Reconciliation
independently rebuilds balances and reservations and reports structured issues.

## Execution-group circuit breaker

Stateful paper fills remain local and use only normalized public order books.
Group creation requires an approved canonical `RiskDecision`; a bare Boolean or
client-provided approval is not accepted. Every fill event is persisted exactly
once by `event_id`. Group reconciliation
uses `residual_qty = buy_filled_qty - sell_filled_qty` and enforces:

- a non-zero residual always means `HEDGE_REQUIRED`;
- `COMPLETED` is impossible while residual exposure is non-zero;
- a protective hedge may request only the absolute current residual;
- a partial hedge is reconciled again against the new residual;
- unavailable/failed hedge execution moves the group and runtime to `HALTED`;
- `HALTED` blocks new execution groups across restart until an explicit reset;
- BUY debits virtual quote only when sufficient; SELL debits virtual base only
  when sufficient; neither path can create a negative balance.

The reset is an audited paper-runtime operation. It does not hide, delete, or
rewrite the failed group or its residual exposure.

## User-visible guarantees and limits

Every result is marked `paper_only`, returns a stable `reason_code` and Russian
`human_reason`, and keeps the live flag false. Audit records actor, time, strategy,
symbol/venue/market, decision, costs, data age, correlation, and algorithm version.
GET `/audit` is read-only and supports pagination and filters.

Paper Alpha does not model exchange latency, settlement, independent leg failures,
network partitions, funding, tax, depeg, or actual fee tiers. Current partial fills
pair both simulated legs to safe common depth; they do not prove real cross-venue
atomicity. Future live admission requires a separate security and operational
acceptance procedure and cannot be enabled by a single environment variable.
