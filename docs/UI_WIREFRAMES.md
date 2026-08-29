# UI Wireframes

## Command center

```text
+------------------------------------------------------------------+
| Quant Trading Platform                    Mode: PAPER  Stop: OK   |
+----------------------+----------------------+--------------------+
| Portfolio            | Daily risk           | Data health        |
| $100,000             | 0.18% / 2.00%        | Binance OK         |
| PnL +$124 paper      | remaining 1.82%      | Bybit OK           |
|                      |                      | OKX delayed 300ms  |
+----------------------+----------------------+--------------------+
| Best opportunities                                                |
| Strategy     Symbol/path        Net %   Notional   Risk   Reason  |
| Triangular   BTC-USDT-ETH       0.18    $1,000     OK     paper   |
| Spread       ETH/USDT           0.09    $700       No     below   |
+------------------------------------------------------------------+
| Decision stream                                                   |
| 12:01:02 approved paper signal BTC-USDT-ETH net 0.18%             |
| 12:01:04 rejected ETH/USDT spread: below minimum edge             |
+------------------------------------------------------------------+
```

## Opportunities

```text
+------------------------------------------------------------------+
| Filters: Strategy [All] Exchange [All] Min net [0.10%] Approved   |
+------------------------------------------------------------------+
| Time     Strategy   Path      Gross  Fees  Slippage Net  Decision |
| 12:01    Triangular BTC/ETH   0.34   0.08  0.05     0.21 Approved |
| 12:02    Spread     ETH/USDT  0.16   0.05  0.04     0.07 Rejected |
+------------------------------------------------------------------+
```

## Risk center

```text
+------------------------------------------------------------------+
| Risk center                                                       |
+------------------+------------------+----------------------------+
| Daily loss limit | Per-trade limit  | Exchange exposure          |
| 0.18% / 2.00%    | $100 current MVP | Binance 34%, Bybit 33%     |
+------------------+------------------+----------------------------+
| Active protections                                                |
| [x] Stale data stop                                               |
| [x] Balance mismatch stop                                         |
| [x] API error pause                                               |
| [x] Live trading locked by default                                |
+------------------------------------------------------------------+
```

## Audit log

```text
+------------------------------------------------------------------+
| Timestamp | Event    | Strategy | Net edge | Decision | Reason      |
| 12:01:02  | Signal   | Triangle | 0.18%    | Approved | paper only  |
| 12:01:04  | Rejected | Spread   | 0.07%    | Rejected | below edge  |
| 12:02:10  | Paused   | System   | -        | Paused   | OKX stale   |
+------------------------------------------------------------------+
```
