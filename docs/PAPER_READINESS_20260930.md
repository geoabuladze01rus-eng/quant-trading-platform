# Ревизия готовности paper crypto — 30.09.2026

**Полный допуск автоматического крипторобота пока не подтверждён.** Этот PR содержит исправления и план проверки; локальный прогон не выполнен. Live trading не реализован и остаётся заблокированным. Стратегии выключены по умолчанию; main не изменён.

## Среда и организация

Доступны GitHub connector и JavaScript orchestration, отсутствуют терминал/Python/Node/Docker/browser и локальный checkout. Git status/remote, пользовательский процесс и SQLite владельца проверить невозможно. Субагенты параллельно проверяли backend, operations, frontend и T-Invest только на чтение. Записи выполняет один интегратор; worktree без filesystem/git tool недоступны.

## Актуальный GitHub

Main HEAD `4e9bbe6866476f863e758a5eb9b6cdc85b0a4213` — merge Paper Alpha foundation #6 от23.09.
Основа этой ветки — PR18 `c9f16e74527390a1b8f7798403c8605e36269ffa`.
Все #7–#18 открыты. Clean означает совместимость со своей базой, не взаимную совместимость стеков.

| PR | HEAD | База | Merge state | Последний CI HEAD |
|---|---|---|---|---|
| [#7](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/7) | `eb5ac0268de2056292397b5f0299c01387420659` | `main` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/35995020921) |
| [#8](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/8) | `da44e7e84ac63fad779ec4b414fe9f418ab60e88` | `feature/premium-light-interface` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36315529742) |
| [#9](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/9) | `b01aa7883765eef503d317bfa28a2b23b3e7231e` | `main` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36092504194) |
| [#10](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/10) | `f415d913fb586bc8c5e17730e852837af7ecc9b0` | `ops/paper-db-backup-verify` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36312462267) |
| [#11](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/11) | `4be65ba0e5fd4788272444cb7c0a9038e0617ec9` | `ops/paper-db-migrations-restore` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36313472287) |
| [#12](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/12) | `84328346f18dafc0fd894993e636aae8d562b540` | `feature/t-invest-sandbox-connection` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36318960210) |
| [#13](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/13) | `5ecdf5ae74e6bcdd2e47786476ccd691d7e50d1c` | `fix/russian-demo-copy` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36319227954) |
| [#14](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/14) | `e514402428f3f4f33bcbef202913882c9998d00a` | `feature/t-invest-sandbox-api` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36319562068) |
| [#15](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/15) | `6d277580877cae7f528a3dd12f340642bfc50068` | `feature/t-invest-sandbox-ui` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36319318065) |
| [#16](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/16) | `73402c42fd10059d934cf602860984fb42960608` | `feature/t-invest-sandbox-api` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36320100711) |
| [#17](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/17) | `17596b341b83be88d0282bd29c58a3eb57c1df79` | `integration/t-invest-sandbox-preview` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36320372598) |
| [#18](https://github.com/geoabuladze01rus-eng/quant-trading-platform/pull/18) | `c9f16e74527390a1b8f7798403c8605e36269ffa` | `main` | clean | [success](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36611021304) |

Последние commits: #7 reduce quote-age rerenders; #8 clarify residual copy; #9 link backup runbook; #10 verified offline restore; #11 safe defaults; #12 read-only OpenAPI; #13 API base URL; #14 documentation newlines; #15 decimal regex; #16 empty token example; #17 account identifier type; #18 cost-gated directional strategy.

В main есть persistent Paper Alpha, публичные стаканы, Decimal accounting, аудит/reconciliation и gated live stubs. Robot, multi-asset directional inventory/walk-forward — только #18. Backup/restore/migrations — #9/#10. Main тёмный; #18 уже содержит собственную светлую тему, поэтому #7/#8 нельзя слепо применять сверху. T-Invest — отдельный стек #11–#17, см. T_INVEST_READINESS_20260930.md.

## Подтверждено кодом #18

- Строго BTC/USDT, ETH/USDT, LTC/USDT; USD/USDC не заменяют USDT.
- Публичные GET Binance depth/exchangeInfo, Bybit market/orderbook/instruments-info, OKX market/books/public/instruments. Private exchange order endpoints не вызываются.
- Paper-only, оба live gates=false, robot/directional defaults=false.
- Decimal strings, WAL, BEGIN IMMEDIATE, durable idempotency и атомарные orders/fills/balances/audit.
- Directional SELL использует собственный strategy_inventory/cost basis. Это не распространяется на legacy spread.
- execute_spot повторно проверяет freshness/future/identity/depth/balance/per-order notional.
- Walk-forward signal prices[:index], fill prices[index]: прямого look-ahead нет.
- Все Python source files #18 просмотрены на news/RSS/economic/calendar/macro и URLs. Получения экономических новостей нет. 12/48 — ценовая эвристика.

Это чтение кода/тестов, не наблюдение пользовательского робота.

## Исправлено в этой ветке

- Terminal mark больше не считается закрытой сделкой walk-forward; benchmark costs multiplicative.
- Sampling/marks/reconciliation/daily checks перенесены внутрь fail-closed exception boundary.
- Regression test на искусственный closed trade; четыре parametrized accounting-cycle errors остаются halted после восстановления метода.
- Directional integration fixture содержит естественный BUY→SELL→BUY, thresholds не ослаблены.
- Portfolio/fills/P&L обновляются последовательным polling. Pending/error accounting блокирует approval.
- Успешная загрузка статусов не объявляется прохождением risk gates; HOLD/SELL не подсвечиваются как положительный edge.

## Оставшиеся риски и воспроизведение

| Приоритет | Файл/сценарий | Требуемое исправление |
|---|---|---|
| P0 для public deployment | Unauthenticated API, Origin не auth | Только loopback; не публиковать API |
| P1 | service.py execute_spot: corrupt ledger/status active либо open order и direct call | Atomic reconciliation/open-order/daily-loss/order-cap/instrument-rules/position gates внутри transaction + concurrency tests |
| P1 | robot.py _daily_pnl: overnight position / unknown unrealized | UTC equity baseline; unknown losses fail closed |
| P1 | api/app.py daily_pnl_usdt=lifetime realized | Настоящий период daily, не lifetime и не cash movement |
| P1 | service.py _funding/execute spread: seeded account продаёт base без strategy BUY | Strategy-funded spread inventory либо отключённый automatic spread. Paired buy возвращает количество, но буквальное требование seed protection нарушено |
| P1 | PR18 store.py user_version2 без schema_migrations; открыть/verify кодом PR10 migrations.py | Единая legacy-aware миграция. Сейчас missing=['schema_migrations'] |
| P1 | PR10 backup.py idle check closes before WAL unlink/os.replace | Restore при остановленной службе; maintenance lock, соблюдаемый writer. Idle check не доказывает отсутствие процесса |
| P1 | frontend/API performance | Closed win rate/drawdown/aligned buy-hold по actual paper fills и «недостаточно данных» отсутствуют |
| P2 | robot.py directional audit/service.py gross metadata | Signal costs round-trip, metadata one-way: default discrepancy0.15pp. Explicit gross/fees/slippage/final-risk/time/age |
| P2 | market_data/service.py/robot.py | Нет cumulative stale/error counters, durable observations; in-memory history max240 |
| P2 | account marks | Нет mark timestamp/age; outage сохраняет старую оценку |
| P2 | full reconciliation, nested orders×fills | Измерить рост БД и задержки длительного запуска |
| P2 | frontend | CSS1120/860/640 есть, actual mobile/browser test не выполнен |

Не считать эти риски устранёнными зелёным CI.

## Фактические проверки

Исходный #18 [run36611021304](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36611021304) проверил synthetic merge `ccdc9f6a5a622b89a9ba0f9ef90cfdcf97e81e8e`.
Raw backend log: Python3.11.16, Ruff0.16.7, mypy2.3.1, pytest9.1.1; **345 passed in10.68s**, secret hygiene/Ruff/mypy36 source files/compileall passed. Frontend npm ci/build и Docker smoke jobs success.
Это исторический hosted CI, не локальный прогон этой сессии. Текущий PR проверяется отдельно в Checks.
Локально Ruff/mypy/pytest/compileall/secret hygiene/npm/Docker не выполнены: runtime tool отсутствует.

## Фактический paper-прогон

**Не выполнен ни online, ни offline.** Длительность, orders/fills/fees/P&L/reconciliation/restart неизвестны.
Для каждой BTC/USDT, ETH/USDT, LTC/USDT observations/BUY/SELL/HOLD/blocked reasons/stale/error = N/A. Не заменять отсутствие измерений нулями.
Тело #18 сообщает прежний smoke180+ observations/0 trades; это заявление автора, не наш прогон.
Короткий smoke/fixture tests не доказательство прибыльности. Команды отдельного диагностического запуска: PAPER_TEST_MAC.md.

## До полного допуска

Atomic gates, seed-spread protection, daily baseline, согласованные migration/restore, прозрачные decisions/statistics/recorder. Затем ограниченная отдельная БД, штатный restart/repeated signal и source telemetry. 0 сделок допустимы; пороги не снижать. Пользовательскую БД и persistent volume не использовать.
