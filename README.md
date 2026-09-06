# Quant Trading Platform

Safe MVP foundation for crypto research (Binance, Bybit, OKX) and the Russian market (T-Invest). Defaults are `MARKET_SCOPE=mixed`, `TRADING_MODE=paper`, and live trading locked. The current connectors use mock data and cannot execute a real trade.

## Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
uvicorn quant_trading_platform.api.app:app --reload
```

The read-only API is available at `http://127.0.0.1:8000` (`/docs` provides OpenAPI documentation).

```bash
cd frontend
npm ci
npm run dev
```

Set `VITE_API_BASE_URL=http://127.0.0.1:8000` to have the dashboard call the mock backend; without it the frontend uses its built-in mock client.

The API does not start collectors on GET requests. Until a producer populates
its in-memory quote cache, `/opportunities` returns HTTP 200 with
`{"status":"no_data","opportunities":[]}`. Compatible quotes return an `ok`
envelope with net edge, source data age, approval and rejection reason. Stale
quotes remain visible as rejected candidates. The MVP cost assumptions are
0.20% combined fees and 0.05% slippage; these are estimates, not venue fee schedules
or depth execution guarantees. `/audit` reads existing events without adding any.
Backend errors are displayed explicitly in the UI and never replaced by mock
approvals. Local Vite origins on port 5173 are allowed by the API's CORS policy.

## Docker

```bash
docker compose up --build
curl http://127.0.0.1:8000/health
```

The container serves FastAPI through Uvicorn on port 8000. Compose uses explicit
paper/sandbox settings with live execution and acceptance disabled. No `.env` or
exchange credentials are needed. Environment files are excluded from the build
context. The published API is read-only, uses mock data, and is intended for local
development; this Compose setup is not an authenticated public deployment.

## Checks

```bash
ruff check .
mypy src
pytest
cd frontend
npm ci
npm run build
```

See [architecture](docs/ARCHITECTURE.md), [safety gates](docs/SAFETY_GATES.md),
the [roadmap](docs/ROADMAP.md), and [competitor lessons](docs/COMPETITOR_LESSONS.md).

We are not copying Cryptohopper. We are building a clearer, safer, and more
transparent trading platform. Signals must explain their logic, net edge must
include fees and slippage, and risk rejection reasons must remain visible.
The full ten product principles are recorded in the competitor lessons;
[agent rules](AGENTS.md) make them implementation requirements.

Autonomous algorithmic trading platform for crypto arbitrage, Russian-market algorithmic strategies, research, backtesting, paper trading, and controlled execution.

## Safety first

This repository starts in research and paper-trading mode. Real-money trading must remain disabled until explicit acceptance gates are passed.

Default project assumptions:

- Crypto venues: Binance, Bybit, OKX
- Russian-market venue: T-Invest API
- Market scopes: crypto, russian_stocks, mixed
- Crypto instruments: BTC/USDT, ETH/USDT, top liquid altcoins
- Russian-market instruments: shares, bonds, ETFs/funds, futures where supported by T-Invest API
- First crypto strategies: triangular arbitrage and inter-exchange spread monitoring
- First Russian-market strategies: portfolio analytics, signal research, paper-trading, and controlled algorithmic execution later
- Maximum future daily loss limit: 2% of portfolio
- API keys must never include withdrawal permissions where the venue supports withdrawals
- Secrets must never be committed

## Roadmap

1. Project scaffold and safety controls
2. Exchange and broker market-data connectors
3. Arbitrage and signal opportunity detector
4. Backtesting engine
5. Paper-trading simulator
6. Risk engine and audit log
7. Controlled live execution after manual approval

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest
```

## Environment

Copy `.env.example` to `.env` and fill only local development values.

```bash
cp .env.example .env
```

Do not commit secrets. Do not enable live trading before acceptance gates are implemented and passed.
