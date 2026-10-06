# Crypto Signal Data Hub — read-only backend

The hub observes markets. It does not recommend a trade, submit an order, use account
credentials, or modify paper accounting. Live execution remains locked.

## Contract

`GET /signal-evidence/BTC%2FUSDT` (also accepts `/signal-evidence/BTC/USDT`).
Allowed canonical symbols: BTC/USDT, ETH/USDT, SOL/USDT. Unsupported symbols return 422;
POST returns 405. Reads compose cached state only: no upstream requests or writes.

The response contains `symbol`, `generated_at_ms`, `spot`, `derivatives`, `liquidations`,
`liquidation_sources`, `quality`, and `execution`. Financial numbers are Decimal internally
and decimal strings in JSON. Optional unavailable fields are null. The execution contract
is always `paper_only: true, live_execution: false`.

The spot section contains fresh depth, per-venue imbalance and midpoint range (not an
executable arbitrage edge). Derivatives contain mark/index/funding/OI, per-venue basis,
index deviation, and comparable previous-snapshot OI changes. Every derived metric names
its contributing venues. There is no BUY/SELL directive or price target.

## Provenance

| Venue | Instruments | Public inputs | OI unit |
|---|---|---|---|
| Binance USD-M | BTCUSDT / ETHUSDT / SOLUSDT | premiumIndex + openInterest | base asset |
| Bybit linear | BTCUSDT / ETHUSDT / SOLUSDT | V5 ticker + open-interest (5min) | base asset |
| OKX SWAP | BTC-USDT-SWAP / ETH-USDT-SWAP / SOL-USDT-SWAP | funding-rate, mark-price, open-interest, index-tickers | contracts |

Requests use fixed public GET endpoints with 5-second per-request timeouts, no redirects,
no inherited client auth, headers or cookies, and sanitized errors. A failure in any
component rejects the entire venue snapshot. Source time is the oldest component time;
all component timestamps are individually checked against receipt time. Mark/index/funding
and OI can have different source cadences; the snapshot uses the conservative oldest time.

## Polling and quality

`DERIVATIVES_DATA_ENABLED=true`, `DERIVATIVES_POLL_INTERVAL_SECONDS=15` (5–300),
`MAX_DERIVATIVES_DATA_AGE_MS=360000`. Six minutes permits Bybit's 5-minute OI snapshots;
consumers needing tighter evidence must reduce this explicitly. Spot freshness retains
`MAX_MARKET_DATA_AGE_MS`. Default spot symbols retain LTC and add SOL.

Independent per-venue/per-symbol polling and bounded backoff prevent a slow or failing
source from blocking another. Errors remove last-good snapshots from usable evidence.
Freshness is rechecked on reads against both source and receipt time.

- Healthy: at least two fresh spot and two fresh derivatives venues, with all expected
  REST sources present and fresh.
- Degraded: the same core minimum is met, but an expected venue is missing/stale.
- Insufficient: either core class has fewer than two independent fresh venues.

Missing/stale source labels identify the evidence class and venue. Absent liquidation
events do not make healthy REST evidence insufficient. Missing data is never a neutral
confirmation. Raw funding rates may have differing intervals; the median is contextual,
not an annualized or interval-adjusted comparison.

## Liquidations and limitations

Public Binance forceOrder, Bybit allLiquidation and OKX liquidation-orders feeds are
partial observations, not market-wide totals. Long/short refer to the liquidated position,
not a trade directive. Summaries cover 5 minutes, 15 minutes and 1 hour per venue and symbol.
Storage is bounded to 10,000 observations in memory, deduplicated by normalized event
content, and not persisted. Identical legitimate events without exchange IDs can be
undercounted. Capacity eviction can truncate a busy window. Source status is separate
from historical observations; a disconnected feed must not be treated as fresh coverage.

Sizes remain venue-native; no cross-venue contract total or fabricated USD notional is
returned. The largest event is expressed in native size. Bybit bankruptcy prices and
exchange liquidation order prices are observations, not guaranteed execution prices.

OKX uses `wss://ws.okx.com/ws/v5/public` on default port 443, never deprecated 8443.
The collector validates subscription acknowledgments with a five-second deadline and uses
OKX textual ping/pong and Bybit JSON ping/pong at a 20-second idle interval. Missing pong
causes reconnect; observed source liveness expires independently of retained events.
All-market traffic for unrelated instruments is filtered without dropping supported batches.
Thread-safe immutable reads avoid concurrent collector/GET deque races.
The collector uses bounded reconnect delay, message size/queue limits, cancellation,
and public subscribe messages only. No raw upstream error is returned.

## Deployment boundary

This implementation is the backend dependency of the separate private MCP/plugin plan.
Plugin packaging/deployment and scheduled Crypto Signal Watch integration are separate
steps. Only the read-only `get_signal_evidence` contract may be exposed through that
adapter. Do not expose the application's unauthenticated paper command API publicly.

## Verification

From the root: `ruff check .`, `mypy src`, `pytest -q`,
`python -m compileall src tests`, `python scripts/check_secret_hygiene.py`.
From frontend: `npm ci`, `npm run build`.
External exchange availability must be checked separately; mocked transport tests do
not establish live public feed availability.
