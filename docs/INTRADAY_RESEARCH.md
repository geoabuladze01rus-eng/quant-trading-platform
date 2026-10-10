# Intraday research (4H trend, 1H review)

Status: research only. Not wired into the API, a scheduler, paper trading or live
execution. Every output carries `paper_only: true` and `live_execution: false`.

## Components

- `strategies/indicators.py` — SMA, EMA, RSI and ATR (Wilder), VWAP. `Decimal` only.
- `strategies/intraday_trend.py` — `intraday_candidate(symbol, candles_4h, candles_1h)`.
- `market_data/intraday_research.py` — `IntradayResearchService`, a per-symbol snapshot.
- `market_data/okx_candles.py` — `fetch(symbol, now_ms=..., bar=...)` for `4Hutc`,
  `1Hutc`, `15m`, `5m` and `1Dutc` (the default).

## Rules

1. **4H context.** `exit_review` when the last 4H close is below EMA 50. Otherwise the
   trend is confirmed only if the close is above EMA 50 and EMA 20 is above EMA 50.
   An unconfirmed trend returns `no_new_entry`.
2. **1H confirmations** (only when the 4H trend is confirmed):
   - close above the highest high of the prior 20 candles (required for any entry);
   - RSI 14 above 50;
   - close above VWAP over the last 24 candles;
   - last volume above the 20-candle average.
3. **Output.**
   - `entry_review` with `intraday_breakout_confirmed` when the breakout and all four
     other checks pass;
   - `entry_review` with `intraday_breakout_partial` when the breakout and at least
     three checks pass (manual review only);
   - otherwise `no_new_entry`.
4. **Stop reference.** Last close minus 2 × ATR(14) on 1H. This is a reference, not an
   order. No take-profit is computed, so an `entry_review` is not a complete signal.
5. **Freshness.** Candles must be completed and aligned to their interval (see
   `validate_candles`). Snapshots older than two polling intervals report `stale`.
   A failed pair is reported as `unavailable` and never hides the other pairs.

## Known limits

- Thresholds are conservative proposals and have not been back-tested.
- The sandbox used to write this code could not reach OKX. The `bar` values were taken
  from the OKX documentation and must be confirmed against a live response before use:
  `https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=4Hutc&limit=2`.
- Symbols are the research universe `BTC/USDT`, `ETH/USDT`, `LTC/USDT`, `SOL/USDT`.
  The paper-trading universe (`SYMBOLS`) remains BTC, ETH and LTC.

## Before wiring into the API or a scheduler

1. Run the full suite in CI: `ruff check .`, `mypy src`, `pytest`.
2. Confirm the OKX `bar` values against a live response.
3. Add API tests for a read-only `GET` endpoint and keep it under the research prefix.
4. Decide the polling owner (for example a Railway service) and document the failure
   behaviour before it runs unattended.
