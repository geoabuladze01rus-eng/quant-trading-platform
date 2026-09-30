# Mac: отдельный диагностический paper-тест

Это инструкция, не результат выполнения. Полный автоматический допуск остаётся заблокирован проблемами PAPER_READINESS_20260930.md. До их устранения используйте только новую временную БД и нулевые seed crypto holdings: это исключает продажу сохранённых монет владельца. 0 сделок допустимы. Не используйте Docker persistent volume/базу владельца.

## 1. Отдельный checkout

Python3.11, Node22 и git должны быть установлены. Не выполнять checkout поверх чужих незакоммиченных файлов.

```bash
mkdir -p ~/quant-paper-review
cd ~/quant-paper-review
git clone --branch fix/paper-readiness-audit-20260930 https://github.com/geoabuladze01rus-eng/quant-trading-platform.git
cd quant-trading-platform
git status --short
git remote -v
git rev-parse HEAD
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints-py311-linux.txt -e '.[dev]'
ruff check .
mypy src
pytest -q
python -m compileall -q src tests
python scripts/check_secret_hygiene.py
```

Linux constraints могут требовать подходящие Mac wheels; ошибки установки зафиксировать, не обходить safety tests.

## 2. Уникальная временная БД

Не копировать .env владельца. Проверить, что .env в новом checkout отсутствует. Эти exports использовать в том же терминале для запуска и restart.

```bash
export PAPER_TRIAL_DIR="$(mktemp -d /tmp/quant-paper-review.XXXXXX)"
export PAPER_DATABASE_PATH="$PAPER_TRIAL_DIR/paper.sqlite3"
export PAPER_ACCOUNT_ID=isolated-crypto-trial
export PAPER_INITIAL_USDT=100000
export PAPER_INITIAL_BTC=0 PAPER_INITIAL_ETH=0 PAPER_INITIAL_LTC=0
export TRADING_MODE=paper MARKET_SCOPE=crypto
export LIVE_TRADING_ENABLED=false LIVE_ORDER_ACCEPTANCE_GATE=false
export PUBLIC_MARKET_DATA_ENABLED=true
export MARKET_DATA_SYMBOLS='["BTC/USDT","ETH/USDT","LTC/USDT"]'
export CRYPTO_PAPER_ROBOT_ENABLED=true CRYPTO_PAPER_DIRECTIONAL_ENABLED=true
git rev-parse HEAD > "$PAPER_TRIAL_DIR/commit.txt"
python --version > "$PAPER_TRIAL_DIR/python.txt"
python -m pip freeze > "$PAPER_TRIAL_DIR/dependencies.txt"
date -u > "$PAPER_TRIAL_DIR/start.txt"
uvicorn quant_trading_platform.api.app:app --host 127.0.0.1 --port 8000
```

Без --reload и только один worker. Порт8000 должен быть свободен; не останавливать чужой процесс. Публичным crypto endpoints ключи не нужны. Не добавлять их.

## 3. Снимки в другом терминале

Второй терминал: установить PAPER_TRIAL_DIR в путь, напечатанный первым (echo "$PAPER_TRIAL_DIR"). Проверить health paper/locked.

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/crypto/paper-robot
curl --fail http://127.0.0.1:8000/paper/orders
curl --fail http://127.0.0.1:8000/paper/fills
curl --fail http://127.0.0.1:8000/paper/performance
curl --fail http://127.0.0.1:8000/paper/reconciliation
curl --fail 'http://127.0.0.1:8000/audit?limit=100&offset=0'
```

Для snapshots повторять этот блок с новым номером каждые10 секунд, максимум3 минуты. Shell переменная PAPER_TRIAL_DIR нужна в этом терминале.

```bash
export PAPER_SNAPSHOT=001
date -u > "$PAPER_TRIAL_DIR/time-$PAPER_SNAPSHOT.txt"
curl --fail http://127.0.0.1:8000/crypto/paper-robot > "$PAPER_TRIAL_DIR/robot-$PAPER_SNAPSHOT.json"
curl --fail http://127.0.0.1:8000/venues > "$PAPER_TRIAL_DIR/venues-$PAPER_SNAPSHOT.json"
curl --fail http://127.0.0.1:8000/paper/orders > "$PAPER_TRIAL_DIR/orders-$PAPER_SNAPSHOT.json"
curl --fail http://127.0.0.1:8000/paper/fills > "$PAPER_TRIAL_DIR/fills-$PAPER_SNAPSHOT.json"
curl --fail http://127.0.0.1:8000/paper/performance > "$PAPER_TRIAL_DIR/performance-$PAPER_SNAPSHOT.json"
curl --fail http://127.0.0.1:8000/paper/reconciliation > "$PAPER_TRIAL_DIR/reconciliation-$PAPER_SNAPSHOT.json"
curl --fail 'http://127.0.0.1:8000/audit?limit=100&offset=0' > "$PAPER_TRIAL_DIR/audit-$PAPER_SNAPSHOT.json"
```

Audit paginated: увеличивать offset100 до total. Snapshot polling не даёт точного cumulative source error/observation счётчика; report это отмечает как недостаток, не выдумывает число.

На90-й секунде Ctrl-C в первом терминале, дождаться shutdown; сохранить снимки. Повторить uvicorn с теми же exports/БД. На180-й секунде Ctrl-C и date -u сохранить end.txt. Сверить orders/fills/idempotency после restart; warmup history ожидаемо сбрасывается. Новые timestamps не являются повторением того же сигнала; durable replay проверяется отдельно тестом.

## 4. UI

```bash
cd frontend
npm ci
npm run build
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1
```

Проверить desktop и viewport375/768: виртуальные деньги, состояние робота, planned action, final block reason, realized/unrealized after costs. Не считать net edge гарантированной прибылью. Portfolio обновляется после завершения всех запросов +10 секунд.

## 5. Offline при недоступной сети

```bash
pytest -q tests/test_crypto_paper_robot.py tests/test_directional_paper_execution.py tests/test_directional_strategy.py tests/test_paper_idempotency.py tests/test_reconciliation.py
```

Это synthetic fixture regression suite, а не recorded live run и не наблюдение процесса владельца. Для требуемого offline paper report нужен отдельный recorder/replayer recorded point-in-time fixtures; он пока не реализован. Не писать 0 orders/P&L если прогон отсутствует.

## 6. Обезличенный отчёт

Указать UTC start/end, SHA/Python/dependencies, online/offline, каждая пара observations/BUY/SELL/HOLD/block reason; orders/fills/fee/slippage, strategy realized/unrealized отдельно от cash, reconciliation, stale/error counts, restart evidence. Неизвестные показатели N/A; нет closed trades — win rate «недостаточно данных». Buy-hold и drawdown actual paper пока отсутствуют, не подменять backtest. Не публиковать SQLite, tokens или account IDs. Новую временную БД сохранить локально для проверки; owner DB/volumes не трогать.
