# Crypto Signal Data Hub MCP — Design

Date: 2026-10-06
Branch: `feat/crypto-signal-data-hub-mcp`
Status: proposed implementation spec
Scope: read-only market evidence for Crypto Signal Watch

## 1. Purpose

Extend the existing Quant Trading Platform with a single read-only evidence layer for
Crypto Signal Watch. The platform already has public, keyless Binance/Bybit/OKX spot
order books, normalization, freshness gates, fail-closed behavior, paper accounting,
and live-trading locks. This design reuses those boundaries instead of creating a second
market-data stack.

The new layer must answer one question:

> What current, independently sourced evidence exists for BTC/USDT, ETH/USDT or SOL/USDT,
> and how trustworthy/fresh is that evidence?

It does not decide that a trade must be opened and it never submits an order.

## 2. Non-goals and safety boundaries

- No private exchange API keys.
- No authenticated account endpoints.
- No real `place_order`, withdrawal, transfer, deposit, margin or leverage action.
- No environment-variable shortcut that can unlock live execution.
- No direct coupling from derivatives evidence to paper/live execution.
- No magic AI score that hides underlying evidence.
- No fabricated value when an exchange omits or delays a field.
- No requirement that all three venues be healthy; degraded sources reduce data quality.
- Existing `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false` and live acceptance locks remain intact.

## 3. Assets and instruments

Primary canonical spot symbols:

- BTC/USDT
- ETH/USDT
- SOL/USDT

Derivatives evidence maps each canonical spot symbol to the most directly comparable
USDT perpetual/swap contract:

- Binance USD-M: BTCUSDT / ETHUSDT / SOLUSDT
- Bybit linear: BTCUSDT / ETHUSDT / SOLUSDT
- OKX SWAP: BTC-USDT-SWAP / ETH-USDT-SWAP / SOL-USDT-SWAP

The canonical API exposed to the rest of the application remains `BASE/USDT`.
Venue-specific derivatives identifiers never escape without venue and instrument type.

## 4. Authoritative public inputs

All requests are public/read-only and credential-free.

### Binance USD-M

Use public USD-M derivatives market data:

- Mark/index/funding snapshot: `GET https://fapi.binance.com/fapi/v1/premiumIndex`
- Open interest: `GET https://fapi.binance.com/fapi/v1/openInterest`
- Funding history only when needed for trend/context:
  `GET https://fapi.binance.com/fapi/v1/fundingRate`
- Liquidation evidence: public USD-M Liquidation Order WebSocket stream.

The current Binance documentation exposes Open Interest and Mark Price/Funding Rate as
public market-data endpoints.

### Bybit V5

Use public linear-contract market data:

- Ticker: `GET https://api.bybit.com/v5/market/tickers?category=linear&symbol=...`
  for mark price, index price, funding rate and current OI where present.
- OI history/latest: `GET https://api.bybit.com/v5/market/open-interest`
- Funding history: `GET https://api.bybit.com/v5/market/funding/history`
- Liquidations: public WebSocket topic `allLiquidation.{symbol}`.

### OKX V5

Use public SWAP market data:

- Public funding-rate endpoint.
- Public mark-price endpoint.
- Public open-interest endpoint.
- Public index ticker where needed.
- Public WebSocket channel `liquidation-orders` with `instType=SWAP`.

Important 2026 transport requirement: OKX announced port 8443 WebSocket discontinuation
for 2026-10-31. New code must use normal `wss://.../ws/v5/public` port 443/default and
must not introduce a new hard-coded `:8443` URL.

The OKX liquidation stream is incomplete by definition; the exchange states that it
does not represent the total number of liquidations. This limitation must be surfaced
in provenance/quality metadata.

## 5. Data model

Add a small immutable normalized derivatives model, using `Decimal` for all financial
values.

Suggested types:

```python
@dataclass(frozen=True)
class DerivativesSnapshot:
    venue: Venue
    symbol: str
    instrument_id: str
    timestamp_ms: int
    received_at_ms: int
    mark_price: Decimal | None
    index_price: Decimal | None
    funding_rate: Decimal | None
    next_funding_time_ms: int | None
    open_interest: Decimal | None
    open_interest_unit: str | None
    source_fields: tuple[str, ...]

@dataclass(frozen=True)
class LiquidationEvent:
    venue: Venue
    symbol: str
    timestamp_ms: int
    side: str
    quantity: Decimal
    price: Decimal | None
    quantity_unit: str
    source_completeness: str
```

No float persistence or computation for money/size fields.

## 6. Collection architecture

Do not modify the existing spot `MarketDataService` responsibilities.

Add a sibling read-only service:

`DerivativesEvidenceService`

Responsibilities:

1. Poll REST derivatives snapshots per venue/symbol at a bounded interval.
2. Maintain independent venue/symbol state.
3. Reject malformed, non-finite, non-positive or future timestamps.
4. Track `received_at_ms` and source timestamp provenance.
5. Mark stale/error data without returning it as healthy evidence.
6. Consume public liquidation WebSocket streams into bounded in-memory rolling windows.
7. Never write trading/accounting state.
8. Never call connector `place_order`.
9. Sanitize upstream exceptions exactly like existing market-data connectors.

REST failures and WebSocket failures are isolated by source. A failed derivatives feed
must not invalidate healthy spot books or another venue.

## 7. Liquidation aggregation

Liquidation data is event data, not a deterministic signal.

Maintain only a bounded rolling window in memory, for example 5m / 15m / 1h aggregates:

- long liquidated notional/size
- short liquidated notional/size
- event count
- largest event
- latest event timestamp
- source completeness label

Do not persist raw liquidation events in the first version.

Do not compare raw contract counts across venues as if they were identical units.
Where a defensible USD notional can be computed from public contract metadata/price,
provide it. Otherwise expose venue-native size and mark cross-venue aggregation as
not comparable.

## 8. Evidence output

Expose one application-level function and one read-only API/MCP contract:

`get_signal_evidence(symbol)`

Allowed symbols: BTC/USDT, ETH/USDT, SOL/USDT.

Response shape:

```json
{
  "symbol": "BTC/USDT",
  "generated_at_ms": 0,
  "spot": {
    "venues": [],
    "cross_venue_spread": null
  },
  "derivatives": {
    "venues": [],
    "funding_consensus": null,
    "open_interest_change": null,
    "mark_spot_basis": null
  },
  "liquidations": {
    "window_5m": {},
    "window_15m": {},
    "window_1h": {}
  },
  "quality": {
    "status": "healthy|degraded|insufficient",
    "fresh_sources": 0,
    "expected_sources": 0,
    "missing": [],
    "stale": [],
    "warnings": []
  },
  "execution": {
    "paper_only": true,
    "live_execution": false
  }
}
```

The response contains evidence, not `BUY`, `SELL`, `LONG`, `SHORT` or a price target.

## 9. Data-quality rules

Quality must be deterministic and explainable, not an opaque weighted AI score.

`healthy`:
- fresh spot depth from at least 2 venues, and
- fresh derivatives snapshot from at least 2 venues, and
- no identity/timestamp validation failure affecting the majority.

`degraded`:
- enough fresh data to inspect the market, but one or more expected evidence classes
  or venues are missing/stale.

`insufficient`:
- fewer than 2 fresh independent venues for the core evidence required by the caller,
  or timestamps/identity cannot be trusted.

Liquidations are supporting evidence only. Their absence alone must not turn otherwise
healthy REST evidence into `insufficient`.

## 10. Cross-venue derived metrics

Compute only metrics with explicit unit compatibility:

- funding median/range across available venues
- mark-vs-spot basis per venue
- index-vs-mark deviation per venue
- OI level per venue
- OI change only when a comparable prior snapshot for the same venue/instrument/unit exists
- spot bid/ask imbalance using existing normalized books
- liquidation imbalance only within a venue or after defensible notional normalization

Every derived field must list the contributing venue set.

## 11. Crypto Signal Watch integration

Crypto Signal Watch uses this Data Hub as one evidence source among:

- higher-timeframe structure/trend
- technical indicators
- news/macro filter
- on-chain/whale evidence
- risk/reward validation

Data Hub must not lower the project rule requiring multiple independent confirmations.

Recommended gating:

- A HIGH/VERY HIGH signal may use derivatives/order-flow confirmation only when
  Data Hub quality is `healthy` or explicitly acceptable `degraded`.
- `insufficient` evidence cannot be counted as a confirmation.
- Missing data must never be interpreted as neutral/bullish/bearish.

## 12. MCP/private plugin boundary

The MCP/private-plugin surface should be intentionally small:

Tool:
- `get_signal_evidence(symbol: enum[BTC/USDT, ETH/USDT, SOL/USDT])`

Optional later tools are out of scope until there is a demonstrated need.

The MCP tool is read-only. It returns no secret, no internal stack trace, no raw auth
header and no write capability.

Deployment/plugin packaging is a separate step after the backend contract is tested.
The plugin should call a safely hosted/read-only Data Hub endpoint or equivalent MCP
transport; the unauthenticated local paper command API must not be exposed publicly.

## 13. Files expected to change

Prefer the smallest diff that fits current architecture.

Likely additions/changes:

- `src/quant_trading_platform/connectors/crypto/client.py`
  - public derivatives REST helpers or narrowly separated derivatives connector module
- `src/quant_trading_platform/market_data/derivatives.py`
  - normalized models/service
- `src/quant_trading_platform/market_data/liquidations.py`
  - bounded public WS collectors/aggregation if separation is warranted
- `src/quant_trading_platform/api/app.py`
  - read-only evidence endpoint only
- `tests/test_crypto_derivatives_public.py`
- `tests/test_derivatives_evidence.py`
- `tests/test_signal_evidence_api.py`
- documentation

If implementation shows that a single focused module is clearer, prefer fewer files.

## 14. TDD requirements

For each new behavior:

1. Write the failing test.
2. Run it and verify it fails for the intended missing behavior.
3. Add minimal production code.
4. Re-run focused test.
5. Run full suite before completion.

Required test classes:

- Binance/Bybit/OKX public request mapping
- no auth/cookies/keys inherited by requests
- malformed payload handling
- Decimal validation
- stale/future timestamp rejection
- source isolation
- symbol/instrument identity validation
- OI/funding/mark/index normalization
- liquidation parsing and bounded retention
- OKX WebSocket URL without deprecated port 8443
- evidence-quality healthy/degraded/insufficient
- no BUY/SELL directive in evidence response
- live execution remains locked
- no connector order method invoked by GET evidence path

## 15. Verification before PR

From repository root:

```bash
ruff check .
mypy src
pytest -q
python -m compileall src tests
python scripts/check_secret_hygiene.py
```

From `frontend/`:

```bash
npm ci
npm run build
```

Run available Docker/public-data smoke checks without enabling live trading.

A PR may claim success only from fresh command output. Network-specific checks that
cannot run must be reported as unverified rather than inferred.

## 16. Delivery sequence

1. Normalized REST derivatives snapshots and tests.
2. Evidence service and deterministic quality model.
3. Public liquidation collectors and rolling aggregation.
4. `GET /signal-evidence/{symbol}` or equivalent internal read-only endpoint.
5. MCP/private plugin adapter exposing only `get_signal_evidence`.
6. Full verification and PR.
7. Separate deployment/plugin connection step.
8. Only after stable operation: wire the evidence source into the scheduled Crypto Signal Watch.

## 17. Success criteria

The feature is complete when:

- BTC/USDT, ETH/USDT and SOL/USDT return normalized spot + derivatives evidence.
- At least Binance, Bybit and OKX are represented independently where their public APIs are available.
- funding, OI, mark/index and liquidation evidence have provenance and freshness.
- missing/stale sources fail closed.
- no private credentials are required.
- no live trade capability is introduced.
- all repository verification checks pass, or any unavailable external smoke is explicitly reported.
- a private MCP/plugin exposes only the approved read-only evidence tool.
