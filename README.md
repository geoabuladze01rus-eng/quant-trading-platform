# Quant Trading Platform

## Automatic OKX spot paper runner

The local Docker Compose profile enables a durable daily-trend paper runner on
the separate `okx-spot-paper` account. It uses completed UTC daily candles for
BTC/USDT, ETH/USDT and LTC/USDT and fresh public OKX order-book depth. No private
exchange order endpoint is used. Relative-strength signals remain research only.

For a direct Python launch, set `OKX_SPOT_AUTO_ENABLED=true`. The library default
is false. See [automatic paper operations](docs/AUTOMATIC_OKX_PAPER.md) for launch,
inspection, exact risk limits and the distinction between fixture verification
and a real-market forward run.

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
By default each venue observes BTC/USDT, ETH/USDT and LTC/USDT. Set
`MARKET_DATA_SYMBOLS=BTC/USDT,ETH/USDT,LTC/USDT` to choose up to ten unique USDT
spot pairs. Each symbol has an independent freshness state; a failed LTC feed
cannot make a last-known LTC price executable. The persistent paired-spread
paper command accepts LTC/USDT with a separately funded virtual LTC balance.
This is still manual paper execution across two venues; no autonomous spot
strategy or unattended order runner is enabled.

### OKX spot paper commands

The separate `okx-spot-paper` virtual account starts with 10,000 USDT and no
crypto. A local client may submit a single manual paper buy or sell against a
fresh public OKX order book. The first supported pairs are BTC/USDT, ETH/USDT
and LTC/USDT. An `Idempotency-Key` is mandatory. The request supplies intent
only; prices, depth, a fixed paper fee estimate (0.10%) and slippage reserve
(0.05%) are server-owned. The full requested quote amount must fit observed
depth and the configured $100 per-order cap. Purchases are additionally limited
to 5% of starting USDT per asset and 10% overall by default. A sale can use only
inventory bought inside this separate virtual account. New commands stop when
realized losses for the UTC day reach 2% of starting USDT by default. Open
position losses are not included in that gate, so this is not yet suitable for
unattended trading.

```bash
curl -X POST http://127.0.0.1:8000/paper/okx/orders \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: replace-with-a-new-uuid' \
  -d '{"symbol":"LTC/USDT","side":"buy","notional_usdt":"10"}'
curl http://127.0.0.1:8000/paper/okx/account
```

The transaction atomically saves balances, one fill, position, audit event,
reconciliation snapshot and idempotency response. No autonomous signal loop,
news filter, real OKX order transport, or unattended deployment is included.
The API remains local and unauthenticated; keep it bound to loopback.
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
[read-only market data](docs/READ_ONLY_MARKET_DATA.md), and
[competitor lessons](docs/COMPETITOR_LESSONS.md).

## OKX spot research

`GET /strategies/spot-signals` exposes cached, read-only daily trend and relative
strength candidates for BTC/USDT, ETH/USDT and LTC/USDT. The background poller
uses only completed UTC daily candles; unavailable or stale data returns no
candidate. These signals never create orders or promise profits. See
[OKX spot research](docs/OKX_SPOT_RESEARCH.md) for rules and backtest limitations.
For multi-year public history and a cost-aware comparison with BTC hold and cash,
run `python scripts/run_spot_research.py --days 1460` in an environment with
access to OKX. This report does not enable automated paper execution.

### Crypto Signal Data Hub

Read-only spot + derivatives + liquidation evidence is available at
`GET /signal-evidence/BTC%2FUSDT` for BTC/USDT, ETH/USDT and SOL/USDT.
It uses public Binance/Bybit/OKX endpoints, requires no API keys and never submits orders.
See [contract, configuration and limitations](docs/CRYPTO_SIGNAL_DATA_HUB.md).
