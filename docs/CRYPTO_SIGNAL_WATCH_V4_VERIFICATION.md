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
