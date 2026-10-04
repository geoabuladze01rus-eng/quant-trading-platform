"""Offline, long-only weekly rotation with next-open fills and explicit costs."""

from dataclasses import dataclass
from decimal import Decimal

from quant_trading_platform.strategies.spot_momentum import (
    DAY_MS,
    SYMBOLS,
    DailyCandle,
    relative_strength_signals,
    validate_candles,
)


@dataclass(frozen=True)
class SimulatedTrade:
    timestamp_ms: int
    symbol: str
    side: str
    quantity: Decimal
    effective_price: Decimal
    fee_usdt: Decimal


@dataclass(frozen=True)
class PortfolioReplay:
    initial_usdt: Decimal
    ending_equity_usdt: Decimal
    return_pct: Decimal
    max_drawdown_pct: Decimal
    fees_usdt: Decimal
    trades: tuple[SimulatedTrade, ...]
    held_symbol: str | None


def replay_rotation(
    series: dict[str, tuple[DailyCandle, ...]],
    *,
    first_signal_index: int = 100,
    initial_usdt: Decimal = Decimal("100000"),
    allocation_pct: Decimal = Decimal("50"),
    fee_pct: Decimal = Decimal("0.35"),
    spread_pct: Decimal = Decimal("0.10"),
    slippage_pct: Decimal = Decimal("0.10"),
    strategy: str = "relative_strength",
) -> PortfolioReplay:
    """Signal on completed close, fill next open; review once per seven UTC days.

    `btc_hold` is a 50%-allocated buy-and-hold benchmark with identical costs.
    A test split can reset capital using historical warmup before the first signal.
    """
    if set(series) != set(SYMBOLS) or strategy not in ("relative_strength", "btc_hold"):
        raise ValueError("Only the three configured spot pairs and benchmark are supported")
    if any(not isinstance(x, Decimal) or not x.is_finite() for x in (
        initial_usdt, allocation_pct, fee_pct, spread_pct, slippage_pct
    )) or initial_usdt <= 0 or not Decimal(0) < allocation_pct <= 50 or any(
        x < 0 or x >= 100 for x in (fee_pct, spread_pct, slippage_pct)
    ):
        raise ValueError("Invalid replay capital or trading costs")
    dates = tuple(c.timestamp_ms for c in series[SYMBOLS[0]])
    if not 100 <= first_signal_index < len(dates) - 1:
        raise ValueError("Insufficient independent test candles")
    for candles in series.values():
        validate_candles(candles, now_ms=dates[-1] + DAY_MS)
        if tuple(c.timestamp_ms for c in candles) != dates:
            raise ValueError("Spot pairs do not share continuous UTC dates")

    cash, quantity, held = initial_usdt, Decimal(0), None
    peak, drawdown, fees = initial_usdt, Decimal(0), Decimal(0)
    trades: list[SimulatedTrade] = []
    half_spread = spread_pct / 200
    slip = slippage_pct / 100
    fee_rate = fee_pct / 100
    for index in range(first_signal_index, len(dates) - 1):
        if (index - first_signal_index) % 7 == 0:
            desired: str | None
            if strategy == "btc_hold":
                desired = "BTC/USDT"
            else:
                candidates = relative_strength_signals({
                    symbol: candles[:index + 1] for symbol, candles in series.items()
                })
                desired = next((s.symbol for s in candidates if s.action == "entry_review"), None)
            if held != desired:
                if held is not None:
                    open_price = series[held][index + 1].open
                    sell_price = open_price * (1 - half_spread - slip)
                    proceeds = quantity * sell_price
                    fee = proceeds * fee_rate
                    cash += proceeds - fee
                    fees += fee
                    trades.append(SimulatedTrade(dates[index + 1], held, "sell", quantity,
                                                 sell_price, fee))
                    quantity, held = Decimal(0), None
                if desired is not None:
                    open_price = series[desired][index + 1].open
                    buy_price = open_price * (1 + half_spread + slip)
                    spend = min(cash, cash * allocation_pct / 100)
                    fee = spend * fee_rate
                    quantity = (spend - fee) / buy_price
                    cash -= spend
                    fees += fee
                    held = desired
                    trades.append(SimulatedTrade(dates[index + 1], desired, "buy", quantity,
                                                 buy_price, fee))
        equity = cash + (
            quantity * series[held][index + 1].close if held is not None else Decimal(0)
        )
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100)
    ending = cash + (quantity * series[held][-1].close if held is not None else Decimal(0))
    return PortfolioReplay(initial_usdt, ending, (ending / initial_usdt - 1) * 100,
                           drawdown, fees, tuple(trades), held)
