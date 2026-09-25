# Quant Trading Platform

Safe Python/FastAPI + React foundation for crypto research and Paper Trading
Alpha. Binance, Bybit and OKX provide keyless public order books. T-Invest is a
separate sandbox/read-only contour. Defaults are `MARKET_SCOPE=mixed`,
`TRADING_MODE=paper`, and `LIVE_TRADING_ENABLED=false`.

There is no real order or withdrawal implementation in this repository. Connector
`place_order` methods remain centrally gated and end in `NotImplementedError` even
if unsafe settings are supplied.

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -c constraints-py311-linux.txt -e '.[dev]'
uvicorn quant_trading_platform.api.app:app --reload
```

The API is at `http://127.0.0.1:8000`; OpenAPI is at `/docs`. On first startup it
idempotently creates a virtual account in `data/paper_alpha.sqlite3`. Override the
local path with `PAPER_DATABASE_PATH`; do not place the database in source control.

```bash
cd frontend
npm ci
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

Without `VITE_API_BASE_URL`, the dashboard uses clearly labelled read-only demo
fixtures and cannot create paper records. Backend errors clear stale approvals
instead of silently replacing them with mock data.

## Paper Alpha

The durable paper workflow is:

`public normalized depth → risk gates → preview → idempotent local command →
balances/reservations + order + fills + positions + audit → reconciliation`.

All financial values use `Decimal` and are returned as exact decimal strings.
`POST /paper/orders` and cancellation require an `Idempotency-Key`. Repeating the
same key and payload returns the original result, including after restart, without
another debit, fill, or audit transition. Reusing a key with another payload is
rejected.

```bash
curl 'http://127.0.0.1:8000/opportunities?explain=true'
curl -X POST http://127.0.0.1:8000/paper/orders/preview \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTC/USDT","buy_venue":"binance","sell_venue":"okx","notional_usdt":"10"}'
curl -X POST http://127.0.0.1:8000/paper/orders \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: replace-with-a-new-uuid' \
  -d '{"symbol":"BTC/USDT","buy_venue":"binance","sell_venue":"okx","notional_usdt":"10"}'
curl http://127.0.0.1:8000/paper/account
curl http://127.0.0.1:8000/paper/reconciliation
curl http://127.0.0.1:8000/paper/residual-exposure
curl 'http://127.0.0.1:8000/audit?limit=50&offset=0'
```

The legacy `POST /paper/orders/simulate` remains available for compatibility as a
volatile, unfunded preview. New product flows use the persistent endpoints. GET
requests are read-only. See [Paper Trading](docs/PAPER_TRADING.md) for accounting,
partial-fill, recovery, and limitation details.

## Public data and Docker

FastAPI lifespan starts independent public REST pollers for Binance, Bybit and OKX.
No trading keys are required. `/venues` distinguishes `no_data`, `stale`, `error`,
and `disabled`; only fresh normalized books can reach the paper engine. T-Invest
remains sandbox/read-only and is never mixed into crypto execution.

```bash
docker compose up --build
curl http://127.0.0.1:8000/health
```

Compose persists the local SQLite file in the `paper-data` volume and explicitly
keeps live execution and the acceptance gate false. This is an unauthenticated
local alpha bound to `127.0.0.1`, not a public deployment. A local Docker smoke
needs a running daemon; CI builds Compose, checks localhost `/health` for paper
mode/live lock, and tears the container down.

Python CI installs exact direct/transitive versions from
`constraints-py311-linux.txt`. Regenerate intentionally for Python 3.11/Linux
with `uv pip compile pyproject.toml --extra dev --python-version 3.11
--python-platform x86_64-unknown-linux-gnu -o constraints-py311-linux.txt`,
then rerun the full suite before committing updated pins. `npm ci` uses the
tracked frontend lock. `python scripts/check_secret_hygiene.py` reports only
file names and violation codes.

## Verification

```bash
ruff check .
mypy src
pytest -q
python -m compileall src tests
cd frontend
npm ci
npm run build
```

Further reading: [architecture](docs/ARCHITECTURE.md),
[safety gates](docs/SAFETY_GATES.md), [roadmap](docs/ROADMAP.md),
[paper database backup and recovery](docs/PAPER_DATABASE_BACKUP.md),
[read-only market data](docs/READ_ONLY_MARKET_DATA.md), and
[competitor lessons](docs/COMPETITOR_LESSONS.md).
