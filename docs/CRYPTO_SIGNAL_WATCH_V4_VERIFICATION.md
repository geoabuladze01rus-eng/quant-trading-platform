# Crypto Signal Watch v4 — verification, 2026-10-08

Branch: `feat/crypto-signal-data-hub-mcp`. Base revision: `35ba3b5`.
Native local implementation; no main change, merge, deployment, live execution or notification switch.

## Stage report

Coverage below is statement coverage measured by the final full repository run. Existing
stages 1–4 were already present at the base revision; they were inspected and verified,
not rewritten. No prior test was removed or weakened.

| Stage | Completed work and files | Tests actually executed | Coverage | Problems / next stage |
| --- | --- | --- | --- | --- |
| 1 Public derivatives | Verified existing `connectors/crypto/derivatives.py`, `market_data/derivatives.py`; no changes | `test_derivatives_models.py` + `test_crypto_derivatives_public.py`: 49 pass | Connector 91.21%; model/service combined 84.42% | Live public API smoke unavailable; stage 2 |
| 2 Evidence service | Verified existing isolated polling/freshness/OI state; no changes | `test_derivatives_evidence.py`: 4 pass | Combined model/service 84.42% | Not all lifecycle branches covered; stage 3 |
| 3 Liquidations | Verified public collectors, bounded 5m/15m/1h native-unit windows; no changes | `test_liquidations.py`: 15 pass | 91.44% | Actual public WS availability unverified; stage 4 |
| 4 Evidence API | Verified existing read-only GET and execution locks; no changes | `test_signal_evidence_api.py`: 6 pass | Composer 98.33% | Actual exchange feed unavailable; stage 5 |
| 5 Intelligence | New `signal_watch/intelligence.py`, `test_signal_intelligence.py` | 19 pass | 97.01% | Provider-specific hosted schemas remain adapter work; stage 6 |
| 6 Journal | New `signal_watch/journal.py`, `test_signal_journal.py` | 5 pass | 100% | SQLite needs durable deployment volume; stage 7 |
| 7 Delivery | New `signal_watch/delivery.py`, `test_signal_telegram_delivery.py` | 4 pass | 95.24% | Disabled interface; no verified Composio send/receipt; stage 8 |
| 8 Watch | New `signal_watch/engine.py`, `service.py`, engine/service tests; modified `api/app.py`, `config.py` | Engine 10 + service 9 pass | Engine 97.40%; service 90.52% | Automatic external plugin ingestion and outcome measurement not connected; stage 9 |
| 9 Full validation | Entire repository, frontend | 510 pass; all checks below exit 0 | Repository 90.88%; new package 95.07% | One pre-existing Starlette deprecation warning; stage 10 |
| 10 PR | Draft PR with architecture, files, verification, limitations and next steps | Git/connector checks, no merge | Not applicable | Hosted acceptance and provider adapters remain |

## Commands and results

Executed in the repository root:

- `ruff check .`: all checks passed.
- `mypy src`: no issues in 54 source files.
- `pytest -q`: 510 passed, one Starlette deprecation warning.
- `python -m compileall src tests`: exit 0.
- `python scripts/check_secret_hygiene.py`: tracked files passed.
- Additional `python -m pytest -q --cov=quant_trading_platform --cov-report=term
  --cov-report=json:...`: 510 passed; 4232 statements, 386 missed; 90.88%.

Executed in `frontend/`:

- `npm ci`: exit 0, 94 packages installed.
- `npm run build`: exit 0; TypeScript + Vite production build completed.
- npm emitted an environment `http-proxy` deprecation warning, not a build failure.

Fresh live public REST smoke attempted Binance/Bybit/OKX snapshots and OKX structure.
All returned sanitized unavailable results from this restricted execution environment.
This does **not** establish that exchange feeds are broken or working in deployment.
Actual public WebSocket sessions and Docker/hosted deployment were not verified.
No Telegram message was sent and no order was created by this change's smoke.

## Review and TDD fixes

New modules were introduced after focused tests failed for missing production modules.
An independent read-only final reviewer inspected the full change. Two important defects
were reproduced with new failing tests, fixed, then checked with green focused and full suites:

- Preserve contributing observation origins/timestamps/strength in durable evidence and
  signal identity; different provenance cannot collapse to the same record.
- Enforce both bounds of the 0.3% Trend Pullback support tolerance; deep breaches cannot
  masquerade as shallow pullbacks. The new valid-touch fixture was corrected from a 1%
  breach to a 0.1% touch; the separate deep-breach regression remains.

An additional TDD regression ensures a weaker, noncontributing origin cannot falsely
satisfy independent confirmation.

## Implementation decisions

- Add a separate read-only package, keeping existing evidence MCP and execution boundaries.
  If this boundary proves unsuitable, the adapter contract must be adjusted.
- Maximum per domain and explicit origins prevent correlated plugin names multiplying score.
  This can conservatively reject candidates and needs observational calibration.
- Predictive profitability and binary scoring are not validated performance claims. Keep
  the disclosed research rubric until actual outcomes justify a reviewed change.
- Live payload compatibility, actual provider independence and hosted delivery require
  external acceptance tests; local mocks cannot establish these properties.
- Telegram remains a disabled injectable boundary, as requested. Automatic outcome
  measurement and vendor-specific adapters remain explicit next steps, not fabricated work.

## Changed files

- `README.md`
- `docs/CRYPTO_SIGNAL_WATCH_V4.md`
- `docs/CRYPTO_SIGNAL_WATCH_V4_VERIFICATION.md`
- `src/quant_trading_platform/api/app.py`
- `src/quant_trading_platform/config.py`
- `src/quant_trading_platform/signal_watch/__init__.py`
- `src/quant_trading_platform/signal_watch/intelligence.py`
- `src/quant_trading_platform/signal_watch/journal.py`
- `src/quant_trading_platform/signal_watch/delivery.py`
- `src/quant_trading_platform/signal_watch/engine.py`
- `src/quant_trading_platform/signal_watch/service.py`
- `tests/test_signal_intelligence.py`
- `tests/test_signal_journal.py`
- `tests/test_signal_telegram_delivery.py`
- `tests/test_signal_watch_engine.py`
- `tests/test_signal_watch_service.py`

## Continuation verification (supersedes totals above)

- New files: `signal_watch/outcomes.py`, `signal_watch/providers.py`,
  `tests/test_signal_outcomes.py`, `tests/test_signal_provider_evidence.py`.
- Modified: `signal_watch/service.py`, `api/app.py`, service tests, both v4 documents.
- Actual final suite: **529 passed**, one existing Starlette warning.
- New focused continuation tests: outcomes 8, provider reports 7, service total 13;
  these 28 tests passed together. All 19 newly added regression cases are in the full run.
- Final statement coverage: repository **90.85%**; signal_watch **93.60%**;
  outcomes **92.00%**; providers **86.67%**; scanner **90.77%**.
- Ruff, mypy (56 source files), compileall and secret hygiene passed. Frontend npm ci and
  npm run build passed again. No live trading, notification send, merge or deployment.
- Independent reviewer found SQLite thread misuse in GET and mixed estimated/observed
  outcome identity. Each was reproduced with a failing regression, fixed, and verified
  by targeted and full green suites. GET now uses cached statistics; kinds use separate tables.
- Actual CryptoAudit technical-analysis and support/resistance tools were called. Both
  responded, but neither supplied an upstream source timestamp. The report boundary
  rejects this as confirmation; no collection-time freshness was invented.
- Vendor-specific hosted transports and scheduled producer ingestion remain unconnected;
  the normalized in-process boundary is now implemented and wired. Live exchange network
  and exact-horizon capture in deployment remain unverified. No claim of realized profit.

## TraderSpy continuation — 2026-10-08 (latest totals)

1. Implemented original-JSON Decimal TraderSpy adapter, completed-candle freshness,
   measured EMA/ADX A evidence, fixed underlying origin, two read-tool allowlist,
   bounded asynchronous collection and cancellation invalidation. Added optional explicit
   Native executor lifespan binding; no executor is automatically discovered or fabricated.
2. Changed files: `signal_watch/traderspy.py` (new), `signal_watch/service.py`,
   `api/app.py`, `tests/test_traderspy_evidence_adapter.py` (new),
   `tests/test_signal_watch_service.py`, and both v4 documents.
3. Observed TDD failures: missing adapter import; missing collection interface; old evidence
   surviving cancellation; failed-refresh cache entering score; missing lifespan binding.
   Final focused adapter/service suite: **32 passed**. Full suite: **548 passed**, one
   existing Starlette deprecation warning. Ruff, mypy (57 source files), compileall,
   secret hygiene, npm ci and frontend build passed. Tests were actually executed locally.
4. Actual statement coverage: repository **91.43%**, signal_watch **92.91%**,
   TraderSpy adapter **87.50%**, scanner **92.91%**.
5. Real hosted TraderSpy BTC candles/indicators were sampled concurrently. Original JSON
   passed adapter and cache at receipt; A strength `0.8794`, upstream completed timestamp
   `1791444660000`, origin `binance_usdm_candles`. This establishes sampled payload
   compatibility, not continuous deployed collection. Independent review found the
   cancellation cache issue; reproduced RED, fixed GREEN, and re-review found no further
   important issue. Generic failed refresh currently excludes all external observations
   for that asset scan, a conservative availability limitation.
6. Next: bind the deployed authorized Native executor; implement individually verified
   producers for other named providers; verify deployed exchange REST/WS and exact-horizon
   observation. CryptoAudit's sampled missing upstream timestamp remains rejected.
   Hosted Telegram delivery is still disconnected and working notification logic unchanged.

LIVE TRADING remains OFF. No key configuration, real order, notification send, merge,
main change or deployment was performed in this continuation.

## Isolated providers and Gina continuation — 2026-10-08 (latest totals)

1. Completed: isolated multi-provider orchestration and explicit Native refresher
   registration; original-JSON Gina candle adapter with Decimal/freshness/identity guards;
   synchronized provider cache reads and invalidation. No autonomous remote-table polling.
2. Changed files: new `signal_watch/collection.py`, `signal_watch/gina.py`,
   `tests/test_signal_provider_collection.py`, `tests/test_gina_evidence_adapter.py`;
   modified `api/app.py`, `signal_watch/providers.py`, `tests/test_signal_watch_service.py`,
   `tests/test_signal_provider_evidence.py`, and both v4 documents.
3. TDD observed missing adapter/orchestrator imports, missing lifecycle binding, and actual
   concurrent invalidation failure (`dictionary changed size during iteration`). Implemented
   fixes passed focused **51 tests**. Full final suite: **575 passed**, one existing
   Starlette deprecation warning. Ruff, mypy (59 source files), compileall and secret
   hygiene passed. Frontend npm ci and npm run build passed. Coverage run also passed 575.
4. Actual statement coverage: repository **91.47%**, signal_watch **93.00%**,
   orchestration **97.14%**, Gina adapter **89.80%**, provider cache **88.57%**.
5. Real Gina public canonical BTC candles were queried via its SQL interface, with OHLCV
   returned as strings. The original JSON passed adapter/cache at receipt, strength `0`,
   source completed timestamp `1791448920000`. This establishes payload compatibility,
   not positive confirmation or continuous operation. TradingCursor's real BINANCE/BTCUSDT
   1m AI response lacked upstream candle time and was not accepted as confirmation.
   Independent review found no critical/important issue. Canonical provenance with legacy
   null venue fields depends on the producer's stipulated canonical table request.
6. Next/blockers: continuously hosted Native executor/refresher binding is not present;
   Gina table reuse/cleanup/refresh is not verified and is not wired as periodic collection;
   other provider-specific producers remain incomplete. Verify deployed public REST/WS and
   exact-horizon observation. Existing notification delivery is unchanged and disconnected
   from these new boundaries. No full-operation claim is made.

The generic failed-refresh availability limitation from the previous continuation is
resolved in production lifecycle through `ProviderCollector`: only the failing provider
is invalidated, while healthy providers remain available. No real orders, API keys,
notification sends, main changes, merges or deployment occurred.

## Table-free Gina Flow and expiry continuation — 2026-10-08 (latest totals)

1. Completed: public Gina order-book producer (one bounded read tool, no table lifecycle),
   original-JSON Decimal notional imbalance for D, complete book guards, source-isolated
   invalidation/cancellation and optional Native lifecycle binding. Added shared-market
   independence grouping, five-second book expiry in cache/scoring, earliest-evidence
   candidate expiry in cached GET, and source-clock/configured freshness for Data Hub flow.
2. Changed files: new `signal_watch/gina_orderbook.py`,
   `tests/test_gina_orderbook_evidence.py`; modified `api/app.py`,
   `signal_watch/intelligence.py`, `signal_watch/providers.py`, `signal_watch/service.py`,
   their three test files, and both v4 documents (11 files).
3. Observed TDD failures: missing producer, missing lifecycle binding, duplicate-market
   independence, stale book contribution/cache, cached accepted candidate past evidence
   expiry, Data Hub response time substituted for source time, invalid source-flow clocks/
   identities/ranges. Final focused suite **79 passed**. Final full suite **609 passed**,
   one existing Starlette deprecation warning. Ruff, mypy (60 source files), compileall and
   secret hygiene passed. Frontend npm ci and npm run build passed. Coverage run passed 609.
4. Actual statement coverage: repository **91.41%**, signal_watch **92.37%**,
   Gina book producer **87.95%**, intelligence **97.26%**, provider cache **87.67%**,
   scanner **92.59%**. These latest measurements supersede earlier totals.
5. Actual hosted Gina BTC book was fetched with depth 5. Original packet passed the full
   producer/cache path at receipt: D `0`, provider snapshot timestamp `1791450065756`,
   origin `hyperliquid_canonical_usdc_depth`. This is sampled compatibility, not positive
   flow confirmation or continuous production operation. Independent review found cached
   candidates outliving depth freshness. Both zero-age and already-aged expiry regressions
   failed first, then passed after the read-only expiry fix. Additional source-time/invalid
   Data Hub flow regressions failed first and passed after correction.
6. Next/blockers: supply and verify a permanent authorized Native executor; complete remaining
   vendor producers and deployed public REST/WS/exact-horizon acceptance. Table reuse/cleanup
   is no longer a prerequisite for Gina D, but remains unverified for optional candle/SMA
   ingestion. Missing upstream timestamps in sampled CryptoAudit/TradingCursor replies
   remain rejected. Telegram delivery and existing notification workflow are unchanged.

No API key, private account call, real order, notification send, main change, merge or
production deployment was performed. Cached GET expiry does not write the journal or
mutate historical candidates.

## Hosted read-only route continuation — 2026-10-08 (latest totals)

1. Completed: fixed the hosted gateway's missing exact GET allowlist entry for
   `/api/crypto-signal-watch`. Verified disabled state and real cached candidates,
   no source polling or journal writes, and blocked command methods/subpaths.
2. Changed files: `src/quant_trading_platform/mcp/hosted.py`,
   `tests/test_hosted_gateway.py`, and both v4 documents.
3. TDD: both new regressions first failed with HTTP 403; the exact allowlist fix passed
   the focused hosted/scanner suite (**33 passed**). Final `pytest -q`: **611 passed**.
   Separate coverage suite also passed 611. Ruff, mypy (60 source files), compileall,
   secret hygiene, frontend npm ci and npm run build passed. One existing Starlette
   TestClient deprecation warning remains; npm reports the inherited http-proxy setting.
4. Actual statement coverage: repository **91.43%**, signal_watch **92.37%**, hosted
   gateway **93.65%**. These measurements supersede previous totals.
5. Independent review found no defect in this delta. Hosted ASGI tests establish local
   route behavior, not deployed availability or continuous external collection.
6. Next/blockers: permanent authorized Native tool-executor binding remains absent from
   the hosted backend; remaining provider-specific producers and deployed public REST/WS
   acceptance remain incomplete. This fix does not resolve or claim those integrations.

Only the requested feature branch is changed. Main, live execution, real orders, API keys,
Telegram delivery and the existing notification workflow remain untouched.

## Large dashboard / diagnostics / acceptance block — 2026-10-08 (latest totals)

1. Completed: cached bounded candidate history with ANTI MISS reasons and stable IDs;
   restart restoration and separate observed/estimated calibration; provider source
   deadlines; v4 dashboard with paper locks, Decimal strings, unavailable/stale states
   and read-only transport; direct-backend snapshot acceptance CLI; frontend tests in
   CI and push coverage for the requested `feat/**` branch family.
2. Changed files (21):

   ```text
   .github/workflows/ci.yml
   docs/CRYPTO_SIGNAL_WATCH_V4.md
   docs/CRYPTO_SIGNAL_WATCH_V4_VERIFICATION.md
   frontend/package.json
   frontend/src/App.tsx
   frontend/src/SignalWatchPanel.tsx
   frontend/src/signalWatch.ts
   frontend/src/styles.css
   frontend/tests/fixtures.mjs
   frontend/tests/loader.mjs
   frontend/tests/register.mjs
   frontend/tests/signalWatch.test.mjs
   frontend/tests/signalWatchPanel.test.mjs
   scripts/check_signal_watch_readiness.py
   src/quant_trading_platform/signal_watch/engine.py
   src/quant_trading_platform/signal_watch/providers.py
   src/quant_trading_platform/signal_watch/readiness.py
   src/quant_trading_platform/signal_watch/service.py
   tests/test_signal_watch_diagnostics.py
   tests/test_signal_watch_pipeline.py
   tests/test_signal_watch_readiness.py
   ```

3. TDD: missing history/cache/provider deadlines/readiness/UI interfaces reproduced
   before implementation; actual expiry-after-probe and oversize-body failures reproduced
   before fixes. Independent review found two important freshness defects: a self-consistent
   stale response could replay as fresh in the browser, and earlier evidence could expire
   during sequential readiness requests. Both were reproduced by failing regressions,
   then fixed in one pass. Final `pytest -q`: **627 passed**, one existing Starlette warning;
   separate coverage run also passed 627. Frontend `npm test`: **11 passed**.
   Ruff, mypy (61 source files), compileall, staged-file secret hygiene, npm ci and build
   passed. npm's inherited http-proxy warning remains. Local runtime: Python 3.12 / Node 24;
   the configured CI Python 3.11 / Node 22 runs are not claimed as locally executed.
4. Actual statement coverage: repository **91.49%**, signal_watch **92.55%**,
   engine **97.53%**, scanner **92.77%**, readiness **92.45%**. Frontend coverage was not
   measured; the 11 tests exercise the transport/model and real server-rendered view.
5. Controlled end-to-end fixtures exercised all normalized external provider boundaries:
   VERY HIGH 100, isolated Gina failure → HIGH 80, Exa failure → rejected 65 / ANTI MISS;
   exact 15m estimated markout and distinct observed samples. This proves local behavior,
   not deployed vendor connectivity, profitability or notification delivery.
   Actual Railway read-only inventory shows `signal-data-hub` and `crypto-signal-mcp`
   sourced from `main`, not the v4 feature branch. No infrastructure setting was changed.
   The actual external CLI attempt returned `incomplete` / unavailable for all endpoints
   from this environment; it does not establish deployment feed health. Docker is absent
   (`docker: command not found`), so Docker smoke remains unverified.
6. Next/blockers: publish and verify an isolated feature-branch hosted runtime; establish
   a permanent authorized Native executor; complete and validate remaining provider
   producers with actual upstream timestamps, sustained REST/WS and exact-horizon data.
   The code is not declared fully operational in production. No notification logic switch,
   API key, private account call, real order, merge or main modification occurred.

## Sampled collection acceptance continuation — 2026-10-08

Completed work: direct/hosted fixed GET paths; original public WS receipt diagnostics;
39 source clocks for three assets; bounded repeated probes with progression, regression,
freshness, cancellation and failed-sample checks. Sampled acceptance requires at least
fifteen observed minutes. Continuous operation remains explicitly unverified.

Changed files in this block:

- `src/quant_trading_platform/api/app.py`
- `src/quant_trading_platform/mcp/evidence_client.py`
- `src/quant_trading_platform/signal_watch/readiness.py`
- `src/quant_trading_platform/signal_watch/acceptance.py`
- `scripts/check_signal_watch_readiness.py`
- `tests/test_signal_evidence_api.py`
- `tests/test_signal_watch_readiness.py`
- `tests/test_signal_watch_collection_acceptance.py`
- `docs/CRYPTO_SIGNAL_WATCH_V4.md`
- `docs/CRYPTO_SIGNAL_WATCH_V4_VERIFICATION.md`

Actual verification after the one-pass review fixes: `ruff check .` passed; `mypy src`
passed for 62 source files; `pytest -q` passed 652 tests; `python -m compileall src tests`
passed; staged `python scripts/check_secret_hygiene.py` passed. `npm ci` installed 94 packages; `npm test` passed 11 tests; `npm run build` passed.
One inherited Starlette TestClient deprecation warning and npm environment http-proxy
warning remain. Python 3.12.14 and Node 24.19.0 were used locally; this is not a claim of
local Python 3.11, Node 22 or Docker execution.

Separate full coverage run: 652 passed; repository 91.58%; signal_watch 93.00%;
acceptance 95.65%; readiness 94.56%. Frontend coverage was not measured.
Independent review found a malformed WS venue causing TypeError; a reproducing RED test
preceded the fix. Adjacent malformed provider symbols also reproduced RED and were fixed.
Both now return unverified/missing evidence instead of crashing. No deferred review minors.

Earlier revision e99e4dc also passed GitHub Actions run 37772343523: Python 3.11 backend
627 tests, Node 22 frontend 11 tests, and Docker localhost paper/live-lock smoke. Those
results apply to that revision; checks for this continuation must be read at its own head.

Problems: actual CryptoAudit sentiment has no upstream timestamp; direct Gina candle
fetch returned chart summary only. These cannot become fresh financial confirmation.
Current hosted deployment and permanent authorized Native executor remain outstanding.
Controlled fifteen-minute clock tests are not a real deployed fifteen-minute observation.
Next stage: bind verified upstream producers in the actual Native host and run deployed
read-only collection acceptance. No main edit, merge, notification switch or order occurred.

## Deployment continuation

See `CRYPTO_SIGNAL_WATCH_V4_DEPLOYMENT.md` for actual hosted preview deployment,
public health/watch/evidence observations and the cancelled, prepared EU migration.
Earlier “not deployed” statements describe earlier revisions; current ca5cbe9 preview
is deployed but full acceptance remains incomplete. Working main service is unchanged.
