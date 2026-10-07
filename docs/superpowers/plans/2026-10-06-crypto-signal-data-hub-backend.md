# Crypto Signal Data Hub Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add normalized, fail-closed Binance/Bybit/OKX derivatives and liquidation evidence for BTC/USDT, ETH/USDT and SOL/USDT, then expose one read-only signal-evidence API without adding any execution capability.

**Architecture:** Reuse the existing public spot books, freshness semantics, Decimal handling and live-trading locks. Add sibling derivatives and liquidation collectors with independent source state, then compose spot + derivatives + liquidation facts into one deterministic evidence payload whose quality is explicit and never emits a trade directive.

**Tech Stack:** Python 3.11, FastAPI, httpx, websockets, dataclasses, Decimal, pytest/pytest-asyncio, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-10-06-crypto-signal-data-hub-mcp-design.md`

## Global Constraints

- Primary signal-evidence symbols are exactly `BTC/USDT`, `ETH/USDT`, `SOL/USDT`.
- Preserve LTC support required by the existing OKX paper runner; the default spot polling universe becomes `BTC/USDT,ETH/USDT,LTC/USDT,SOL/USDT` rather than replacing LTC.
- Public/read-only exchange data only; no private API keys or authenticated account endpoints.
- No real `place_order`, withdrawal, transfer, deposit, margin or leverage action.
- Existing `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false` and live acceptance locks remain intact.
- All financial values use `Decimal`; no floats for prices, rates, quantities or notionals.
- Missing/stale/invalid data fails closed and is never interpreted as neutral/bullish/bearish.
- New OKX WebSocket URLs must not contain `:8443`; default `wss://`/443 only.
- Liquidations are supporting evidence only and must carry source-completeness limitations.
- Evidence response must never contain a BUY/SELL/LONG/SHORT directive or price target.

## Review Focus

- Exchange sends a syntactically valid response for the wrong symbol/instrument: reject the snapshot, do not relabel it.
- One REST call inside a multi-call venue snapshot succeeds and a later call fails: venue state becomes error; never emit a partially trusted snapshot.
- Open-interest units differ across venues/contracts: preserve venue-native unit and never aggregate incompatible raw values.
- Liquidation WebSocket reconnects after duplicated/out-of-order events: bounded store must deduplicate stable event identities where available and reject future timestamps.
- Evidence is requested while spot data is fresh but derivatives are stale, or vice versa: deterministic `degraded`/`insufficient` quality must explain exactly which source class failed.

---

### Task 1: Normalized derivatives snapshots and public adapters

**Files:**
- Create: `src/quant_trading_platform/connectors/crypto/derivatives.py`
- Create: `src/quant_trading_platform/market_data/derivatives.py`
- Modify: `src/quant_trading_platform/connectors/crypto/__init__.py`
- Modify: `src/quant_trading_platform/market_data/__init__.py`
- Test: `tests/test_crypto_derivatives_public.py`
- Test: `tests/test_derivatives_models.py`

**Interfaces:**
- Produces: `DerivativesSnapshot`, `normalize_derivatives_snapshot(...)`.
- Produces: `PublicDerivativesSource.get_snapshot(symbol: str) -> DerivativesSnapshot`, `close() -> None`.
- Produces concrete sources: `BinanceDerivativesSource`, `BybitDerivativesSource`, `OKXDerivativesSource`.

- [ ] **Step 1: Write failing normalization tests**

Add tests that assert:
- canonical symbol normalization to `BASE/USDT`;
- venue-specific `instrument_id` is retained;
- `Decimal` values reject NaN, Infinity and negative/zero price/OI values;
- future source timestamps reject;
- optional missing fields remain `None` rather than invented;
- wrong instrument identity rejects.

- [ ] **Step 2: Run focused model tests and verify RED**

Run: `pytest tests/test_derivatives_models.py -v`  
Expected: FAIL because derivatives model/normalizer does not exist.

- [ ] **Step 3: Implement immutable normalized model**

Implement in `market_data/derivatives.py`:

`DerivativesSnapshot(venue, symbol, instrument_id, timestamp_ms, received_at_ms, mark_price, index_price, funding_rate, next_funding_time_ms, open_interest, open_interest_unit, source_fields)`

and

`normalize_derivatives_snapshot(...) -> DerivativesSnapshot`.

Keep validation local and deterministic; do not add persistence.

- [ ] **Step 4: Run model tests and verify GREEN**

Run: `pytest tests/test_derivatives_models.py -v`  
Expected: PASS.

- [ ] **Step 5: Write failing exchange-mapping tests**

Use `httpx.MockTransport` to verify exact public requests and parsing for:
- Binance USD-M `/fapi/v1/premiumIndex` + `/fapi/v1/openInterest`;
- Bybit V5 linear ticker + open-interest endpoint;
- OKX public SWAP funding/mark/open-interest/index inputs.

Assert no inherited `authorization`, cookies, Binance/Bybit/OKX API-key headers or signatures, including when the injected client itself has auth/default headers.

Add the Review Focus test: second request failure after first request success yields one sanitized `MarketDataError`, not a partial snapshot.

- [ ] **Step 6: Run adapter tests and verify RED**

Run: `pytest tests/test_crypto_derivatives_public.py -v`  
Expected: FAIL because public derivatives sources do not exist.

- [ ] **Step 7: Implement minimal public derivatives sources**

Implement one bounded `get_snapshot(symbol)` per venue. Use fixed GET endpoints, five-second request timeout, `auth=None`, `follow_redirects=False`, sanitized errors, explicit symbol mapping and no private credentials.

Do not add funding-history or OI-history polling yet; current normalized snapshots are sufficient for this task.

- [ ] **Step 8: Run Task 1 tests**

Run:
`pytest tests/test_derivatives_models.py tests/test_crypto_derivatives_public.py -v`  
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/quant_trading_platform/connectors/crypto src/quant_trading_platform/market_data tests/test_derivatives_models.py tests/test_crypto_derivatives_public.py
git commit -m "feat: add public derivatives snapshots"
```

### Task 2: Derivatives polling service and deterministic data quality

**Files:**
- Modify: `src/quant_trading_platform/market_data/derivatives.py`
- Modify: `src/quant_trading_platform/config.py`
- Test: `tests/test_derivatives_evidence.py`

**Interfaces:**
- Consumes: `PublicDerivativesSource.get_snapshot(symbol)`.
- Produces: `DerivativesEvidenceService`, `MultiDerivativesEvidenceService`, `DerivativesSourceState`.
- `DerivativesSourceState` retains current and immediately previous comparable snapshot for same-venue OI deltas; it never compares OI across different units/instruments.
- Produces read-only methods: `snapshot() -> list[dict[str, object]]`, `fresh_snapshot(venue, symbol) -> DerivativesSnapshot | None`.

- [ ] **Step 1: Write failing service-state tests**

Cover:
- independent Binance/Bybit/OKX failure isolation;
- stale/future data removal from fresh evidence;
- no GET/read side effects;
- slow venue not delaying healthy venues;
- source close on shutdown;
- Review Focus case: fresh spot-equivalent source class is irrelevant; derivatives service judges only its own fresh venue state.

- [ ] **Step 2: Run focused service tests and verify RED**

Run: `pytest tests/test_derivatives_evidence.py -v`  
Expected: FAIL because service classes do not exist.

- [ ] **Step 3: Implement bounded polling service**

Match the existing `MarketDataService` lifecycle and backoff style. Add conservative configuration:
- `derivatives_data_enabled: bool = True`
- `derivatives_poll_interval_seconds` bounded to a safe public-REST cadence;
- `max_derivatives_data_age_ms` as an explicit freshness limit.

Set the existing default `market_data_symbols` to `BTC/USDT,ETH/USDT,LTC/USDT,SOL/USDT` so SOL spot depth is available without removing LTC required by the current paper runner. Derivatives polling itself is restricted to BTC/ETH/SOL.

Do not modify paper accounting or execution code.

- [ ] **Step 4: Run service tests and verify GREEN**

Run: `pytest tests/test_derivatives_evidence.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quant_trading_platform/market_data/derivatives.py src/quant_trading_platform/config.py tests/test_derivatives_evidence.py
git commit -m "feat: poll derivatives evidence safely"
```

### Task 3: Public liquidation parsing, rolling aggregation and collectors

**Files:**
- Create: `src/quant_trading_platform/market_data/liquidations.py`
- Test: `tests/test_liquidations.py`

**Interfaces:**
- Produces: `LiquidationEvent`.
- Produces parsers:
  - `parse_binance_liquidation(payload: object) -> tuple[LiquidationEvent, ...]`
  - `parse_bybit_liquidation(payload: object) -> tuple[LiquidationEvent, ...]`
  - `parse_okx_liquidation(payload: object) -> tuple[LiquidationEvent, ...]`
- Produces: `LiquidationWindow.add(event)`, `summary(symbol, now_ms) -> dict[str, object]`.
- Produces async collectors with `start() / stop()` and fixed public WebSocket URLs.

- [ ] **Step 1: Write failing parser and rolling-window tests**

Assert:
- side semantics are normalized correctly per venue;
- wrong symbol/contract rejects;
- malformed/non-finite quantities reject;
- future timestamps reject;
- 5m/15m/1h expiry works;
- largest event/latest timestamp are correct;
- incompatible native quantity units are not cross-venue summed;
- duplicate stable event payloads do not inflate rolling totals;
- Binance symbol/all-market force-order payloads are handled only for allowed symbols;
- Bybit `allLiquidation.{symbol}` array events parse;
- OKX liquidation metadata carries `source_completeness="partial_exchange_stream"`.

- [ ] **Step 2: Run liquidation tests and verify RED**

Run: `pytest tests/test_liquidations.py -v`  
Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement parsers and bounded store**

Use `Decimal`, bounded deques and deterministic pruning. Preserve native quantity unit when a defensible USD notional cannot be computed.

- [ ] **Step 4: Add failing collector lifecycle tests**

Use a fake WebSocket transport/iterator to test:
- reconnect without unbounded busy-loop;
- source failure isolation;
- clean cancellation/stop;
- no auth/login messages;
- OKX URL contains `wss://.../ws/v5/public` and never `:8443`.

- [ ] **Step 5: Implement minimal public collectors**

Use existing `websockets` dependency only. Keep collector outputs in memory and never persist raw liquidation events.

- [ ] **Step 6: Run Task 3 tests**

Run: `pytest tests/test_liquidations.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/quant_trading_platform/market_data/liquidations.py tests/test_liquidations.py
git commit -m "feat: collect public liquidation evidence"
```

### Task 4: Compose one signal-evidence contract

**Files:**
- Create: `src/quant_trading_platform/market_data/signal_evidence.py`
- Modify: `src/quant_trading_platform/api/app.py`
- Test: `tests/test_signal_evidence_api.py`
- Test: `tests/test_paper_api.py`

**Interfaces:**
- Consumes existing `MultiMarketDataService.book_for_simulation(...)`.
- Consumes `MultiDerivativesEvidenceService` and liquidation summaries.
- Produces: `get_signal_evidence(symbol: str, *, spot, derivatives, liquidations, generated_at_ms: int) -> dict[str, object]`.
- Produces GET route: `/signal-evidence/{symbol}` for the three allowed symbols only.

- [ ] **Step 1: Write failing pure composition tests**

Assert exact quality behavior:
- `healthy`: >=2 fresh spot venues and >=2 fresh derivatives venues;
- `degraded`: usable core evidence with missing/stale expected sources;
- `insufficient`: fewer than two independent fresh venues for required core evidence.

Assert response identifies `missing`, `stale`, warnings and contributing venues.

Add Review Focus test: incompatible OI units remain per-venue and never get a fake cross-venue total.

- [ ] **Step 2: Run evidence tests and verify RED**

Run: `pytest tests/test_signal_evidence_api.py -v`  
Expected: FAIL because composer/route do not exist.

- [ ] **Step 3: Implement evidence composer**

Compute only unit-safe derived metrics:
- per-venue mark/spot basis;
- per-venue index/mark deviation;
- funding median/range over available numeric rates;
- per-venue OI and same-venue OI change only after a comparable prior snapshot exists;
- liquidation windows as supporting evidence.

Never emit `BUY`, `SELL`, `LONG`, `SHORT` or a price target field.

- [ ] **Step 4: Wire lifespan and GET route**

Start/stop derivatives/liquidation services alongside existing read-only market-data services. GET route reads state only and never triggers upstream I/O or writes.

- [ ] **Step 5: Add execution-boundary regression tests**

In `tests/test_signal_evidence_api.py` and existing paper API allowlist tests assert:
- route is GET-only;
- no new POST path exists;
- monkeypatched `PublicCryptoConnector.place_order` fails test if invoked;
- live trading remains locked;
- response contains `paper_only=true`, `live_execution=false`.

- [ ] **Step 6: Run Task 4 tests**

Run:
`pytest tests/test_signal_evidence_api.py tests/test_paper_api.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/quant_trading_platform/market_data/signal_evidence.py src/quant_trading_platform/api/app.py tests/test_signal_evidence_api.py tests/test_paper_api.py
git commit -m "feat: expose read-only signal evidence"
```

### Task 5: Documentation and full backend verification

**Files:**
- Modify: `README.md`
- Modify: `docs/READ_ONLY_MARKET_DATA.md`
- Create: `docs/CRYPTO_SIGNAL_DATA_HUB.md`

**Interfaces:**
- Documents the stable GET evidence contract consumed by the separate MCP/plugin plan.

- [ ] **Step 1: Document provenance and limitations**

Document:
- venue/instrument mapping;
- public endpoint classes;
- freshness and quality states;
- OI-unit limitation;
- liquidation incompleteness;
- OKX 443 requirement;
- no private keys and no execution path.

- [ ] **Step 2: Run full backend verification**

Run from repository root:

```bash
ruff check .
mypy src
pytest -q
python -m compileall src tests
python scripts/check_secret_hygiene.py
```

Run from `frontend/`:

```bash
npm ci
npm run build
```

Expected: all commands exit 0. Any unavailable external-network or Docker smoke must be named explicitly rather than inferred.

- [ ] **Step 3: Run available read-only smoke**

Start only the safe local app configuration and inspect `/health`, `/venues`, and `/signal-evidence/BTC%2FUSDT` (or FastAPI-safe equivalent path/query contract chosen in Task 4). Confirm live trading remains locked and no write is performed.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md docs/READ_ONLY_MARKET_DATA.md docs/CRYPTO_SIGNAL_DATA_HUB.md
git commit -m "docs: document crypto signal data hub"
```

- [ ] **Step 5: Stop for backend review**

Do not merge to `main`. Backend is now the dependency for the separate private MCP/plugin plan.
