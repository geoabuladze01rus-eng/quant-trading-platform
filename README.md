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
npm install
npm run dev
```

Set `VITE_API_BASE_URL=http://127.0.0.1:8000` to have the dashboard call the mock backend; without it the frontend uses its built-in mock client.

## Checks

```bash
ruff check .
mypy src
pytest
cd frontend && npm run build
```

See [architecture](docs/ARCHITECTURE.md), [safety gates](docs/SAFETY_GATES.md), and the [roadmap](docs/ROADMAP.md).

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
