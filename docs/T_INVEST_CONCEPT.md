# T-Invest Integration Concept

T-Invest is approved as the second trading venue family for the project. It must be implemented as a separate Russian-market connector, not mixed into the crypto exchange connector logic.

## Role in the platform

T-Invest is not part of crypto arbitrage execution. It supports a separate market scope for Russian-market algorithmic strategies:

- portfolio analytics;
- market-data collection;
- backtesting;
- paper trading;
- controlled algorithmic execution after safety gates;
- audit and tax/accounting data collection.

## Architecture

```text
Dashboard
  -> Backend API
  -> Risk Engine
  -> Connector router
       -> Crypto: Binance / Bybit / OKX
       -> Russian market: T-Invest API
```

## Market scopes

- `crypto`: crypto exchanges only;
- `russian_stocks`: T-Invest only;
- `mixed`: dashboard and backend can observe both scopes, but strategies remain isolated.

## MVP boundaries

The first T-Invest milestone must not place real orders.

Allowed in MVP:

- read accounts;
- read portfolio;
- read instruments;
- read candles and market data;
- collect operations history;
- paper-trading simulation;
- risk reporting.

Not allowed in MVP:

- live market orders;
- live limit orders;
- live stop orders;
- margin trading;
- automatic strategy execution with real funds.

## Safety gates before live execution

Live T-Invest execution can be considered only after:

1. sandbox integration works;
2. portfolio and operations reconciliation works;
3. order placement is covered by tests;
4. risk limits are enforced before order creation;
5. daily loss stop is implemented;
6. audit log records every decision;
7. manual owner approval is added;
8. emergency stop is tested.

## UI requirements

The dashboard must show T-Invest as a separate venue group:

- Crypto venues: Binance, Bybit, OKX;
- Russian market: T-Invest;
- separate PnL and exposure by market type;
- separate strategy lists;
- separate risk limits by scope;
- one global emergency stop.

## Implementation notes

Recommended connector path:

```text
src/quant_trading_platform/connectors/t_invest/
```

The connector should expose a narrow internal interface:

- `get_accounts()`;
- `get_portfolio(account_id)`;
- `get_instruments()`;
- `get_candles(figi, interval, from_time, to_time)`;
- `place_order()` only after live gate approval;
- `cancel_order()` only after live gate approval.

Secrets:

- `T_INVEST_API_TOKEN`;
- `T_INVEST_ACCOUNT_ID`;
- `T_INVEST_SANDBOX=true` by default.
