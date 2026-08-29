# Quant Trading Platform

Autonomous algorithmic trading platform for crypto arbitrage research, backtesting, paper trading, and controlled execution.

## Safety first

This repository starts in research and paper-trading mode. Real-money trading must remain disabled until explicit acceptance gates are passed.

Default project assumptions:

- Exchanges: Binance, Bybit, OKX
- Instruments: BTC/USDT, ETH/USDT, top liquid altcoins
- First strategies: triangular arbitrage and inter-exchange spread monitoring
- Maximum future daily loss limit: 2% of portfolio
- API keys must never include withdrawal permissions
- Secrets must never be committed

## Roadmap

1. Project scaffold and safety controls
2. Exchange market-data connectors
3. Arbitrage opportunity detector
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

Do not use API keys with withdrawal permissions.
