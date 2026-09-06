# Read-only market data

The application starts three independent public REST pollers from FastAPI lifespan.
Each source retains one normalized snapshot for one configured symbol. GET endpoints
inspect state without making upstream requests. A failed source is removed from the
opportunity cache while other venues keep polling; the last good price may still be
displayed as a stale/error reference. Shutdown waits for in-flight timed requests
and closes owned HTTP clients. Error retries back off up to 30 seconds.

## Sources and normalization

| Source | Public request | Source timestamp |
| --- | --- | --- |
| Binance | `GET https://api.binance.com/api/v3/depth` (100 levels) | Local request start; exchange time unavailable |
| Bybit | `GET https://api.bybit.com/v5/market/orderbook?category=spot` (50 levels) | Matching-engine `cts`, falling back to `ts` |
| OKX | `GET https://www.okx.com/api/v5/market/books` (100 levels) | Snapshot `ts` |

Official references: [Binance depth](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints),
[Bybit orderbook](https://bybit-exchange.github.io/docs/v5/market/orderbook),
[OKX orderbook](https://app.okx.com/docs-v5/en/#rest-api-market-data-get-order-book).

`NormalizedOrderBook` contains sorted Decimal price/quantity levels, canonical symbol,
venue, market_type, timestamp_ms, received_at_ms and timestamp_source. `to_quote()`
maps the top levels to `MarketQuote`. Invalid/crossed/empty depth, duplicate prices,
nonfinite or nonpositive numbers, future/stale timestamps and venue/market mismatches
are rejected. These are full REST snapshots, not WebSocket deltas.

Binance does not report exchange time in this endpoint. Request-start age includes
network latency, but cannot prove upstream book freshness. UI exposes that limitation.
The 1000 ms default freshness gate is conservative; all hosts need synchronized clocks.

Crypto calls never attach credentials, auth headers or cookies, never follow redirects,
and only issue GET requests to fixed endpoints with five-second HTTP timeouts.
Real balances are unavailable through these public adapters. Fee values are labelled
paper estimates, not account-specific charges. All place_order methods remain blocked
or unimplemented even when configuration gates are set.

## T-Invest preparation

`TInvestClient` now accepts an injectable `SandboxReadTransport`. Its allowlist contains
only sandbox account/portfolio reads, instruments, candles and order books. It requires
sandbox mode and fails clearly without a configured transport; it no longer fabricates
portfolio data. No authenticated HTTP implementation or real token was added. The UI
therefore reports sandbox/no_data. Russian stocks remain separate from crypto detectors.

The transport must target the sandbox service when implemented. Reference:
[T-Invest sandbox service](https://developer.tbank.ru/invest/api/sandbox-service).
Fixtures test RPC mapping and verify that order methods never reach transport.

## Verification and limits

Tests use injected HTTP transports with deterministic venue payloads; they cover mapping,
normalization, timestamps, malformed responses, denied/redirect/timeout requests,
credential isolation, source failure isolation, startup/shutdown and cache-to-risk API flow.
The live smoke on this change received valid BTC/USDT books from all three crypto venues.
This is a connectivity check, not a sustained availability or execution benchmark.

No trading, withdrawals or persistent order execution is introduced. Portfolio loss,
balance and exposure inputs are still not connected to real accounts. Fees/slippage in
opportunities are estimates; public book availability is subject to venue rate limits,
regional restrictions and network outages. Use one application worker for this MVP:
multiple server processes each start their own pollers and caches.
