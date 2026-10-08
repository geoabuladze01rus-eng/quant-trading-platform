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
