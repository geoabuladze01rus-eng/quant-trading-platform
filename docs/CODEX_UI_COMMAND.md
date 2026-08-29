# Codex UI Implementation Command

Use this command when asking Codex to build the first web dashboard.

```text
Build the first UI for quant-trading-platform as a professional risk-first trading dashboard.

Product context:
- The platform is an autonomous crypto arbitrage and algorithmic trading system.
- It supports Binance, Bybit and OKX.
- The first MVP must not allow real-money trading.
- The default mode is PAPER.
- Live trading must remain locked until acceptance gates are implemented.

Create a frontend dashboard with these screens:
1. Command center
2. Opportunities
3. Risk center
4. Audit log
5. Settings

Design requirements:
- professional trading terminal style;
- graphite / black / steel gray palette;
- green only for confirmed positive net edge;
- red only for losses, blocked states and hard stops;
- amber for warnings;
- dense readable tables;
- no casino-style animations;
- no manual buy/sell buttons in MVP.

Command center must show:
- current mode;
- safety state;
- portfolio value;
- paper PnL;
- daily loss limit usage;
- exchange data health;
- best opportunities;
- recent decision stream;
- emergency stop placeholder.

Opportunities page must show:
- strategy;
- symbol/path;
- buy/sell venue;
- gross spread;
- fees;
- slippage;
- expected net profit;
- available notional;
- data age;
- risk decision;
- rejection reason.

Risk center must show:
- max daily loss;
- per-trade notional limit;
- per-asset exposure;
- per-exchange exposure;
- stale data events;
- API errors;
- rejected orders;
- balance mismatches.

Audit log must show every bot decision with timestamp, strategy, market snapshot ID, risk checks, decision and reason.

Settings page must allow non-secret configuration only. Do not expose or edit API secrets in the UI.

Use mock data first. Keep the frontend ready to connect to backend API later.
```
