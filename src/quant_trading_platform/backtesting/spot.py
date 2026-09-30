"""Conservative next-open spot replay; research accounting is separate from paper ledger."""

from dataclasses import dataclass
from decimal import Decimal

from quant_trading_platform.strategies.spot_momentum import (
    DailyCandle,
    trend_signal,
    validate_candles,
)


@dataclass(frozen=True)
class BacktestResult:
    ending_equity_usdt: Decimal
    return_pct: Decimal
    max_drawdown_pct: Decimal
    trades: int
    fees_usdt: Decimal
    final_base_quantity: Decimal


def replay_trend(
    symbol: str,
    candles: tuple[DailyCandle, ...],
    *,
    initial_usdt: Decimal = Decimal("100000"),
    fee_pct: Decimal = Decimal("0.35"),
    slippage_pct: Decimal = Decimal("0.10"),
    allocation_pct: Decimal = Decimal("50"),
) -> BacktestResult:
    """Signal on completed close at t; fill at next open with explicit costs.

    This is a model, not an OKX fill simulator. No intraday stop is assumed.
    """
    validate_candles(candles, now_ms=candles[-1].timestamp_ms + 86_400_000)
    if len(candles) < 102:
        raise ValueError("At least one next-day fill is required")
    if (not initial_usdt.is_finite() or initial_usdt <= 0
            or any(not x.is_finite() or x < 0 for x in (fee_pct, slippage_pct))
            or not allocation_pct.is_finite() or not Decimal(0) < allocation_pct <= 50
            or fee_pct >= 100 or slippage_pct >= 100):
        raise ValueError("Invalid backtest capital, costs, or allocation")
    cash, base, fees = initial_usdt, Decimal(0), Decimal(0)
    peak, drawdown, trades = initial_usdt, Decimal(0), 0
    for index in range(100, len(candles) - 1):
        signal = trend_signal(symbol, candles[:index + 1])
        next_open = candles[index + 1].open
        if signal.action == "entry_review" and base == 0:
            spend = min(cash, (cash + base * next_open) * allocation_pct / 100)
            fee = spend * fee_pct / 100
            base = (spend - fee) / (next_open * (1 + slippage_pct / 100))
            cash -= spend
            fees += fee
            trades += 1
        elif signal.action == "exit_review" and base > 0:
            proceeds = base * next_open * (1 - slippage_pct / 100)
            fee = proceeds * fee_pct / 100
            cash += proceeds - fee
            fees += fee
            base = Decimal(0)
            trades += 1
        equity = cash + base * candles[index + 1].close
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100)
    ending = cash + base * candles[-1].close
    return BacktestResult(ending, (ending / initial_usdt - 1) * 100, drawdown,
                          trades, fees, base)
