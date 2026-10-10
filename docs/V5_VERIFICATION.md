# v5 direct paper control verification — 2026-10-09

Branch: feat/direct-okx-control, based on feature v4 commit
898565581cb678983a288a31da421046f3217c64. Main unchanged; no merge, live bot
control, real orders, exchange credentials or production deployment.

## Completed work and files

- BotManager: all eight requested methods, three-attempt transient retry/backoff,
  savepoint rollback, request/response diagnostics and durable command identity.
  Files: okx_controller/__init__.py, okx_controller/manager.py, bot_registry.yaml.
- Decision/risk engine: EXECUTE/IGNORE/STOP/CLOSE, HIGH/VERY_HIGH score consistency,
  RR, regime/volatility/ATR mapping, UTC daily risk, exposure, drawdown, cooldown,
  current state, position overlap and persistent duplicates.
  File: execution_engine.py.
- Watch→Router→paper control integration: control intent before command, one
  transaction for state/risk/audit/outbox, independent notification worker,
  original freshness deadline, observed-level RR and 14-bar ATR.
  Files: api/app.py, config.py, signal_watch/engine.py,
  signal_watch/service.py, signal_watch/notifications.py.
- Documentation/packaging: README.md, ARCHITECTURE.md, DIRECT_OKX_CONTROL_V5.md,
  this report, pyproject.toml, constraints-py311-linux.txt, Dockerfile,
  Dockerfile.hosted, .github/workflows/ci.yml.
- Regression/TDD: tests/test_direct_okx_control.py. Existing tests were retained.

## Actually executed checks

Python 3.12.3 in the local repository .venv:

| Check | Result |
|---|---|
| black --check --line-length 100 (new engine/controller and integration tests) | Passed; 4 files unchanged |
| ruff check . | Passed |
| mypy src | Passed; 67 source files |
| pytest -q with whole-backend coverage | 739 passed, 1 Starlette TestClient deprecation warning; 27.72 s |
| Direct-control integration suite with --cov-branch --cov-fail-under=95 | 46 passed; 100% statement and branch coverage |
| python -m compileall src tests | Passed |
| python scripts/check_secret_hygiene.py | Passed after staging all new files |
| frontend npm ci | Passed; 94 packages |
| frontend npm run build | Passed; TypeScript and Vite |
| frontend npm test | 11 passed |

Coverage scope: the new Execution Engine and okx_controller modules have 309
statements and 98 branches, all covered by the 46 integration cases. Whole-backend
statement coverage is 92.15%; the >=95% CI gate explicitly targets the new control
modules. It does not assert >=95% for unrelated pre-existing modules.

Integration cases exercise start, ignore, stop, close, duplicate, cooldown,
notification and audit, plus strict Decimal/input validation, disabled registry,
drawdown, overlap, restart persistence, bounded retry/exhaustion, savepoint rollback,
missing RR target, expired evidence, live/nontransactional Router rejection and
full rollback when audit insertion fails. The full Watch test verifies that
paper control completes while a notification transport is deliberately blocked,
then checks worker delivery, shutdown and direct volatility STOP without TradingView.

## Latency experiment

Ten local paper START decisions followed by CLOSE ran against a temporary WAL
SQLite journal. Total process() latency measured externally, including atomic
commit: minimum 132 microseconds, maximum 526 microseconds, mean 205 microseconds.
All ten returned PAPER_APPLIED. No real OKX request was made. The integration test
also asserts end-to-end local paper control completes in under one second.

## Limits and next steps

- TradingView is absent from the control chain and can be disconnected. No actual
  OKX API write transport exists: under LIVE OFF / no real orders, lifecycle commands
  are local paper operations. Real account bot control is not claimed tested.
- GRID/DCA here are lifecycle definitions and risk mapping, not invented fills,
  profits or a claim that an exchange grid is running. PAIR_BTC_ETH remains DISABLED.
- STOP retains allocated capital; only explicit CLOSE releases the local reservation.
- Runtime registry state is in SQLite; YAML is the immutable registry seed.
- Native notification bindings remain necessary. The three-channel integration test
  uses receipt-returning doubles. Actual v5 Push/Email/Telegram E2E has not been run;
  the previously unavailable ChatGPT Push acknowledgement remains a host blocker.
- The existing scheduled workflow and deployed preview were not switched.
- Healthy local command latency meets the test target. Real exchange latency,
  retry-backoff duration and external message latency are not covered by that target.

The implementation branch is reviewable with the original v4 feature as its base.
Future work must separately define any exchange demo capability, authorization and
acceptance procedure; LIVE remains disabled by this change. Bind a genuine Native
Push receipt transport to complete real three-channel delivery acceptance.
