# T-Invest API sandbox — фактическая готовность 30.09.2026

Крипто paper account и T-Invest API sandbox — разные контуры. Это не обычный брокерский терминал. Ни реальные брокерские заявки, ни новые sandbox-заявки этой сессией не отправлялись; токен владельца не доступен и в чате не запрашивается.

## Матрица

| Возможность | Main4e9bbe6 | Интегрированная ветка #17 /17596b34 | Настоящий API sandbox-счёт |
|---|---|---|---|
| accounts/portfolio | Injectable Protocol scaffold; без HTTP transport | HTTP connector + GET API + UI | Не проверен |
| positions/orders read | Нет рабочего network API | GET API/connector | Не проверен |
| price preview | Нет | GET estimate, approved:false, order_submission_available:false | Не проверен |
| submit/cancel sandbox | Нет | Low-level methods; gate=false; API/UI writes отсутствуют | Не проверен |
| UI | T-Invest панели нет | Светлая панель/estimate | Browser/mobile не проверены |
| local preflight | Нет | Offline configuration check; --check-accounts opt-in read | С владельческим токеном не выполнен |

## Цепочка

#11 foundation4be65ba0 → база #10 backup/migrations.
#12 read-only API84328346 → #11.
#13 UI5ecdf5ae → #8 Russian/light stack; API #12 нужна отдельно.
#14 price previewe5144024 → #12.
#15 preview UI6d277580 → #13; требует #14.
#16 integration73402c42 → #12, включает #14 и light/UI stack.
#17 account/preflight17596b34 → #16; исправляет account contract на id.
Все открыты/draft/mergeable clean; последний HEAD CI каждого success. Полные SHA и ссылки — PAPER_READINESS_20260930.md.

## Что действительно проверено

Main client.py: accounts/portfolio/instruments/candles/orderbook scaffold, default transport absent. Fixture tests не подтверждают network integration.
#17 sandbox.py: sandbox-invest-public-api.tbank.ru, SandboxService allowlist, redirects выключены, errors sanitized, paper/sandbox/token/live-disabled gates.
api/t_invest_sandbox.py: только GET.
t_invest_preflight.py: блокирует unsafe mode, оба live gates и sandbox order flag; без --check-accounts сеть не вызывается. Вывод без token/account IDs.
[CI36320372598](https://github.com/geoabuladze01rus-eng/quant-trading-platform/actions/runs/36320372598): raw log secret hygiene/Ruff/mypy37 files/compileall success, **347 passed in2.92s**; frontend/Docker jobs success. Это hosted CI, не локальный или API-account тест.

## Блокеры

- P0 для будущего submit: нет authentication, durable replay protection/idempotency, audit/reconciliation. Low-level method создаёт новый UUID без request_id. ORDERS_ENABLED оставить false.
- P1: возможности не в main; интеграция crypto#18 и migrations#10 конфликтует по схеме.
- P1: status.ready отражает конфигурацию, не доступность provider/account.
- P1: настоящий sandbox account не проверен.
- P2: actual mobile/browser test не выполнен.

## Путь к безопасной проверке собственного счёта

Отдельный checkout #17 (не эта crypto ветка) и отдельный PAPER_DATABASE_PATH. Установить Python3.11/dev dependencies по README ветки. Создать API sandbox-счёт через официальные средства T-Invest; обычный production brokerage account не использовать. Токен настроить только локально через безопасное хранилище/неотслеживаемый файл; не показывать в shell history, чате или репозитории.
TRADING_MODE=paper, LIVE_TRADING_ENABLED=false, LIVE_ORDER_ACCEPTANCE_GATE=false, T_INVEST_SANDBOX=true, T_INVEST_SANDBOX_ORDERS_ENABLED=false.
Настройки read-only enabled взять из .env.example именно #17; не переносить случайные env между ветками.

```bash
python -m quant_trading_platform.t_invest_preflight
python -m quant_trading_platform.t_invest_preflight --check-accounts
```

Первый — конфигурация; второй только читает accounts, не открывает/пополняет/отправляет/отменяет заявки. После успеха отдельно проверить GET portfolio/orders/estimate согласно docs/T_INVEST_SANDBOX.md #17, сохранить обезличенные результаты/время/SHA/error codes. Не печатать token/account IDs.

**Ответ:** чтение собственного API sandbox-счёта можно проверить на #17 после приватной локальной настройки; реальная готовность этого счёта пока не подтверждена. В main полноценной интеграции нет. Отправка sandbox order через платформу не готова.
