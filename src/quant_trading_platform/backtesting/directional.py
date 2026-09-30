"""Small walk-forward validator for the fixed directional paper strategy."""

from dataclasses import dataclass
from decimal import Decimal

from quant_trading_platform.strategies.directional import SignalSide, directional_signal


@dataclass(frozen=True)
class WalkForwardReport:
    samples: int
    closed_trades: int
    winning_trades: int
    win_rate_pct: Decimal
    strategy_return_pct: Decimal
    buy_hold_return_pct: Decimal
    max_drawdown_pct: Decimal
    average_trade_net_pct: Decimal

    def approved(
        self,
        *,
        min_closed_trades: int,
        min_average_trade_net_pct: Decimal,
        max_drawdown_pct: Decimal,
    ) -> bool:
        return (
            self.closed_trades >= min_closed_trades
            and self.average_trade_net_pct >= min_average_trade_net_pct
            and self.strategy_return_pct > 0
            and self.max_drawdown_pct <= max_drawdown_pct
        )


def walk_forward(
    prices: tuple[Decimal, ...],
    *,
    fee_pct: Decimal,
    slippage_pct: Decimal,
    short_window: int = 12,
    long_window: int = 48,
) -> WalkForwardReport:
    """Evaluate each signal only on observations available before its fill."""
    if not prices:
        return WalkForwardReport(
            0, 0, 0, Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0)
        )
    if any(not price.is_finite() or price <= 0 for price in prices):
        raise ValueError("Prices must be finite and positive")
    one_way_cost = (fee_pct + slippage_pct) / 100
    cash = Decimal(100)
    quantity = Decimal(0)
    entry_cash = Decimal(0)
    trade_returns: list[Decimal] = []
    peak = cash
    max_drawdown = Decimal(0)
    for index in range(long_window, len(prices)):
        signal = directional_signal(
            prices[:index],
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
            short_window=short_window,
            long_window=long_window,
        )
        execution_price = prices[index]
        if signal.side == SignalSide.BUY and quantity == 0:
            entry_cash = cash
            quantity = cash / (execution_price * (1 + one_way_cost))
            cash = Decimal(0)
        elif signal.side == SignalSide.SELL and quantity > 0:
            cash = quantity * execution_price * (1 - one_way_cost)
            trade_returns.append((cash / entry_cash - 1) * 100)
            quantity = Decimal(0)
        equity = cash + quantity * execution_price * (1 - one_way_cost)
        peak = max(peak, equity)
        if peak:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
    # Mark the remaining position; valuation is not a closed strategy trade.
    terminal_equity = cash + quantity * prices[-1] * (1 - one_way_cost)
    strategy_return = terminal_equity - Decimal(100)
    buy_hold = (
        prices[-1] / (prices[0] * (1 + one_way_cost))
        * (1 - one_way_cost) - 1
    ) * 100
    wins = sum(value > 0 for value in trade_returns)
    closed = len(trade_returns)
    return WalkForwardReport(
        len(prices),
        closed,
        wins,
        Decimal(0) if not closed else Decimal(wins) / closed * 100,
        strategy_return,
        buy_hold,
        max_drawdown,
        Decimal(0) if not closed else sum(trade_returns, Decimal(0)) / closed,
    )
