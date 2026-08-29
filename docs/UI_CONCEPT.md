# UI Concept

The interface must support disciplined, risk-first algorithmic trading. It should not encourage impulsive manual trading. The user must always understand whether the platform is observing, simulating, or allowed to execute real orders.

## Product principle

The interface is a mission-control dashboard for an autonomous quant platform:

- show trusted market state;
- show opportunities only after fees, slippage, latency and liquidity filters;
- make risk limits visible before profit metrics;
- explain why the bot entered, skipped, paused, or stopped;
- keep live trading locked behind explicit acceptance gates.

## Core screens

### 1. Command center

Purpose: one-screen status of the whole platform.

Key blocks:

- Trading mode: research, backtest, paper, live locked, live enabled
- Global safety state: ok, warning, paused, stopped
- Portfolio value and daily PnL
- Daily loss limit consumption
- Active exchanges and data health
- Current best opportunities
- Recent decisions and rejections
- Emergency stop control

The most important visual element is not profit. It is risk state.

### 2. Opportunities

Purpose: inspect arbitrage signals before execution.

Table columns:

- strategy
- symbol/path
- buy venue
- sell venue
- gross spread
- estimated fees
- estimated slippage
- expected net profit
- available notional
- latency age
- risk decision
- reason

Filters:

- strategy
- exchange
- symbol
- minimum net profit
- risk-approved only

### 3. Strategy lab

Purpose: configure and compare strategies without touching live execution.

Sections:

- triangular arbitrage
- inter-exchange spread monitor
- spot/futures basis
- funding rate arbitrage
- statistical pairs

Each strategy card must show:

- status
- enabled in research/backtest/paper/live
- minimum net edge
- max notional
- liquidity threshold
- last signal count
- last rejection reason

### 4. Backtest

Purpose: prove whether a strategy has historical edge.

Required outputs:

- equity curve
- drawdown curve
- trades table
- Sharpe ratio
- Sortino ratio
- max drawdown
- win rate
- profit factor
- average slippage assumption
- fee model used

Backtest reports must be exportable.

### 5. Paper trading

Purpose: simulate live execution with real-time data but no real orders.

Required views:

- virtual portfolio
- virtual orders
- expected vs simulated fill price
- missed opportunities
- rejected opportunities
- latency and data freshness

### 6. Execution control

Purpose: tightly control real trading when allowed.

Live trading controls must be visually separated and locked by default.

Required gates:

- all tests pass
- paper trading period completed
- max daily loss configured
- API keys have no withdrawal permission
- manual approval confirmed
- emergency stop tested

### 7. Risk center

Purpose: central risk supervision.

Metrics:

- daily loss used
- per-trade limit
- per-asset exposure
- per-exchange exposure
- stale data events
- API errors
- rejected orders
- balance mismatches
- exchange health

### 8. Audit log

Purpose: explain every automated decision.

Each event must show:

- timestamp
- strategy
- market data snapshot ID
- decision type: signal, approve, reject, order, cancel, pause, stop
- reason
- risk checks
- expected net profit
- actual or simulated result

## Navigation

Recommended left navigation:

1. Command center
2. Opportunities
3. Strategy lab
4. Backtest
5. Paper trading
6. Risk center
7. Audit log
8. Settings

## Visual style

The product should look like a professional trading terminal, not a casino app.

Style direction:

- dark professional theme by default;
- graphite, black, steel gray, muted blue accents;
- green only for confirmed positive net edge;
- red only for loss, stop, blocked, or danger;
- yellow/amber for warnings;
- dense but readable data tables;
- no decorative gradients or flashy profit animations.

## Status model

Primary platform states:

- `RESEARCH`: data collection and research only
- `BACKTEST`: historical simulation
- `PAPER`: live market simulation, no real orders
- `LIVE_LOCKED`: live controls visible but disabled
- `LIVE_ENABLED`: live execution allowed after gates
- `PAUSED`: temporary stop due to warning
- `STOPPED`: hard stop after critical risk event

## First MVP UI

The first UI should include only:

- command center dashboard;
- opportunities table;
- risk center summary;
- audit log;
- settings page for non-secret configuration.

No real order button in the first public MVP.

## Non-goals for MVP

Do not build:

- manual scalping interface;
- social trading;
- copy trading;
- P2P marketplace;
- mobile app before the web dashboard is stable;
- live trading controls before acceptance gates exist.
