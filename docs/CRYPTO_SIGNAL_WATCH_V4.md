# Crypto Signal Watch v4

Read-only candidate research. No order submission and no Telegram dispatch from the
background scanner. LIVE TRADING remains locked. Main is not modified.

## Architecture

Existing public derivatives / liquidation / spot evidence is retained as specified in
`superpowers/specs/2026-10-06-crypto-signal-data-hub-mcp-design.md` and
`superpowers/plans/2026-10-06-crypto-signal-data-hub-backend.md`.

The new `signal_watch` package has independent boundaries:

- `intelligence.py`: timestamped Decimal observations, domain contributions and rejection reasons.
- `journal.py`: SQLite candidate journal with exact Decimal strings and stable signal identity.
- `engine.py`: setup checks, rejection journaling, ANTI MISS and observed-outcome calibration.
- `service.py`: public OKX completed 1m/1H structure and background scanning of BTC/ETH/SOL.
- `delivery.py`: opt-in Composio Telegram transport interface with durable delivery attempt records.

The app starts the scanner when `SIGNAL_WATCH_ENABLED=true` (default), public market data
is enabled, and the market scope includes crypto. `SIGNAL_WATCH_INTERVAL_SECONDS` defaults
to 30 and is bounded to 15–300 seconds. The separate journal is `signal_watch.sqlite3`
beside the configured paper database. No liquidation events are persisted there.

`GET /crypto-signal-watch` returns cached candidate diagnostics only. It performs no
upstream I/O, accounting writes or notification delivery. Cached data older than 60
seconds or from the future is hidden as stale. Existing `GET /signal-evidence/{symbol}`
and the single-tool evidence MCP contract are unchanged.

## Confidence contract

An observation requires source, domain, Decimal strength in [0,1], timestamp, and explicit
independent origin. Supported source names: Market Structure, TraderSpy, Data Hub,
CryptoAudit, TradingCursor, Gina, Exa, Blockscout. Source name alone is not independence.
The external provider adapter is a callable supplied to `WatchService.external`; absent
providers supply no observations. No provider response is invented.

| Domain | Weight |
| --- | ---: |
| A Market Structure | 25 |
| B Levels | 20 |
| C Trigger | 20 |
| D Flow | 20 |
| E Context | 15 |

Each domain takes its strongest fresh contribution, not a sum across plugins. Only origins
of contributions attaining a domain maximum count toward independent confirmation.
Duplicate observations cannot increase scores. A/B/C must each have a positive contribution,
and at least two independent contributing origins are required. HIGH is score >=72 and
<84; VERY HIGH is >=84. Lower scores are rejected. All arithmetic uses Decimal.

Data Hub observations are accepted only for healthy quality, or degraded when explicitly
allowed by the caller; the background scanner does not opt in. Insufficient, fail-closed
and unknown quality never count as a Data Hub confirmation.

The built-in structure adapter contributes binary A/B/C confirmations only when a setup
is detected: positive hourly trend, measured levels and completed-candle trigger. Flow
strength is mean positive spot depth imbalance over available independent public venues.
No Context contribution is fabricated. Full timestamped external observations can augment
these measurements through the adapter contract. This deterministic baseline is a research
rubric, not an independently validated prediction of profitability.

## Setups and diagnostics

- Trend Pullback: positive hourly trend, support touch within 0.3%, completed close recovering
  above support and prior close.
- Breakout + Retest: prior close above measured resistance, retest within 0.3%, completed
  close holding above resistance in positive trend.
- Liquidity Sweep: current low below prior low and completed close above prior low.
- Momentum: positive trend, five-bar return >=1%, volume >=1.5 times prior twenty-bar mean,
  and close above prior close.

Structure uses twenty-bar levels excluding the breakout/trigger bars, hourly SMA20 trend,
completed contiguous candles, and exact volume/price calculations. Malformed, incomplete,
stale or future series fail closed. Asset fetches run independently.

Every scan journals all four setups, including rejected candidates. The stable signal ID
includes asset/setup/time/regime/domain contributions and provenance. ANTI MISS records
confirmed setups blocked by quality/core evidence/score; 65–<72 scores are labelled
`near_threshold`. It never promotes a rejected candidate.

`record_outcome` accepts a separately observed net return after candidate time. Calibration
reports sample count, positive outcomes and exact mean net return by confidence bucket.
It does not adjust thresholds or treat missing outcomes as wins. A blocked setup log is
not proof that a profitable opportunity was missed.

## Telegram boundary

`ComposioTelegramTransport` is an injected, authorized transport, not a bot-token client.
Its `send_message(chat_id, text, signal_id)` must return a verified provider message ID.
`TelegramDelivery` defaults to disabled; no integration is wired into the existing
notification workflow. A caller must explicitly enable this boundary.

Delivery identity is `(signal_id, chat_id)` and persisted before attempting the send.
Delivered IDs survive restarts. A timeout/unverified response is `unknown`; cancellation
or crash may leave `pending`. Neither state is automatically resent. External verification
is required to reconcile an uncertain write. This is at-most-once *attempt* semantics;
exactly-once external delivery cannot be guaranteed without provider reconciliation.

## Verification and remaining integration

Tests use mock public REST transports, fake WebSockets and actual SQLite files. Full
repository checks must be run before publishing changes. No test substitutes for a live
exchange/network smoke or an actual Telegram receipt.

This implementation accepts normalized observations from all named providers. Their
hosted account connections and provider-specific schema adapters are not automatically
installed or invoked by this repository. The default scanner uses public structure + Data
Hub. The Composio transport remains an interface, not a verified hosted send integration.

Remaining work: verify live public REST/WS availability in the deployment environment;
provide authorized provider-schema adapters; validate predictive quality with observed
outcomes; approve and connect the new delivery interface separately. No deployment,
notification switching, merge, live execution or private exchange keys are part of this PR.

## Continuation: strict reports and automatic forward markouts

`ProviderEvidenceCache` accepts independently timestamped normalized reports from the
approved provider names. Each report has a canonicalizable symbol and up to five unique
domain observations, each with domain, exact strength, origin and source timestamp.
Raw JSON numeric tokens are parsed as Decimal. Python float inputs, missing provenance,
missing/future/stale timestamps and malformed reports fail closed. Failed sources clear
their own evidence without invalidating other providers. The cache is wired to the scanner
through its existing external callback. GET exposes cached per-provider/asset quality.
This is an in-process producer boundary; hosted vendor-specific transports/ingestion are
still not connected. A real sampled CryptoAudit analysis/levels response had no source
timestamp, so it cannot be promoted to fresh confirmation using receipt time.

`OutcomeTracker` now automatically registers confirmed setup candidates (including those
blocked by evidence/score), and observes subsequent completed-candle closes in the scanner.
The default horizon is exactly 15 minutes. The exit candle must have exactly the due
close timestamp, be fresh, belong to the same asset, and contain a positive Decimal price.
Missing the exact horizon becomes `missed_horizon`, never a substituted later return.
Pending and measured records survive restart; duplicate observations do not add samples.

Per-side estimated fee is 0.1%, per-side estimated slippage is 0.05%. These disclosed defaults
are research assumptions, not measured exchange costs. Estimated net return is
`exit * (1-fee-slippage) / (entry * (1+fee+slippage)) - 1`.
The kind is always `estimated_forward_markout`: no order/fill or realized execution P&L
is implied. Estimates live only in `forward_markouts`; separately supplied observed outcomes
remain in `signal_outcomes`. One kind cannot reserve/block the other's identity.
The public statistics include only measured forward markouts. The scanner computes these
statistics on SQLite's owning event-loop thread; GET reads a detached in-memory cache.

External report callback failures now produce a sanitized warning while retaining healthy
structure and candidate rejection diagnostics. No notification workflow is switched.

## TraderSpy Native producer

`signal_watch/traderspy.py` adapts the original JSON text from the public
`traderspy_get_candles` and `traderspy_get_technical_indicators` read tools. It validates
symbol, 1-minute timeframe, completed consecutive candles, upstream close timestamps,
matching indicator candle/price, EMA periods and directional ranges. JSON numeric tokens
are parsed as Decimal; provider AI confidence and prose are ignored. Bullish EMA ordering
and positive directional dominance contribute `min(ADX / 50, 1)` to A only. The origin is
`binance_usdm_candles`, so the provider name cannot masquerade as an independent market.

An embedding Native host may explicitly assign an asynchronous callable to
`app.state.read_only_tool_executor` before application lifespan begins. Signature:
`async execute(tool_name: str, arguments: dict[str, object]) -> object`.
Return the original MCP result, including its JSON text content block. The producer invokes
only the two allowlisted read tools above. No credentials or trading tool configuration is
accepted. Without this host binding, the producer stays disconnected; ordinary FastAPI
cannot access the chat tool runtime automatically.

The lifespan binds the producer to the scanner when public crypto scanning is enabled.
Collection is bounded per asset; errors and cancellation invalidate TraderSpy's cached
report for that asset. Failed collection excludes external evidence for that scan and
exposes a sanitized warning while retaining local structure/rejection diagnostics. After
collection, the scoring clock is refreshed. GET never collects or sends notifications.
A real hosted BTC response passed this adapter/cache boundary during development. Continuous
host deployment, other vendor producers and delivery acceptance remain unverified.

## Isolated Native refreshers and Gina candle ingestion

An embedding host can register `app.state.signal_watch_external_refreshers` before
lifespan startup: a mapping from approved external provider name to
`async refresh(symbol: str) -> bool`. Each refresher must validate its actual upstream
response and update `app.state.signal_watch_providers`; return exactly `True` only for
successful collection. Registered callbacks are explicitly authorized host code, not
arbitrary discovered tools. When a read-only executor is also supplied, the built-in
TraderSpy refresher is installed through the same orchestration boundary.

`ProviderCollector` runs refreshers independently with an 8-second deadline inside the
scanner's 10-second deadline. Failure, malformed return, timeout or cancellation clears
only that provider/asset evidence. Orchestration returning `True` means isolated collection
completed; it does not mean every provider succeeded. Per-provider cached quality records
show errors. Other healthy providers, including independently supplied reports, remain
available. Direct single-collector injection still excludes all external observations on
failure; the production lifespan now uses isolated orchestration.

`ProviderEvidenceCache` synchronizes updates, invalidation, observation reads and quality
snapshots with one reentrant lock. Background ingestion and FastAPI worker reads cannot
iterate a mutating dictionary or read inconsistent status/value records. GET still performs
no upstream I/O and no persistent write.

`gina.adapt_gina_candles` ingests original JSON row results from Gina's read-only SQL query
on a canonical Hyperliquid candle table. The producer must create/query canonical coin,
1m, closedOnly data, without HIP-3 provider context, and use the exact returned table name.
Do not infer canonical provenance from an arbitrary table: actual sampled legacy venue
fields were null, so this request binding is part of the producer's required contract.
Query OHLCV with `CAST(... AS VARCHAR)` to avoid Python float calculations, retaining
vendor-supplied numeric precision; upstream table precision cannot be reconstructed.
No Hyperliquid USDC price is relabelled as a USDT price. Only measured structure evidence
is produced, with origin `hyperliquid_canonical_usdc_candles`.

The adapter requires 25–50 consecutive completed candles, asset/timeframe identity,
consistent inclusive close timestamps, observedAt not in the future, source freshness,
finite prices/volume and valid ranges. A gets strength 1 only when last close > SMA20 >
previous SMA20; otherwise 0. AI summaries and chartRendered receipts are not confirmation.
A real fresh BTC SQL sample passed adapter/cache with strength 0, establishing compatibility,
not bullish confirmation. No periodic remote table creation is wired: table reuse, cleanup
and hosted refresh must be verified before enabling a continuous producer. Liquidation
storage remains exclusively in memory.

TradingCursor was sampled with BINANCE/BTCUSDT/1m. Its response provided AI completion time,
levels and a recommendation, but no upstream candle timestamp. It is not accepted as fresh
confirmation and its recommendation is not copied into the engine or evidence API.
