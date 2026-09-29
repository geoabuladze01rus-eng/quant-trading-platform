from decimal import Decimal

from quant_trading_platform.backtesting import walk_forward
from quant_trading_platform.strategies import SignalSide, directional_signal


def test_directional_strategy_requires_history_and_cost_adjusted_edge() -> None:
    warmup = directional_signal(
        tuple(Decimal(index) for index in range(1, 20)),
        fee_pct=Decimal("0.1"),
        slippage_pct=Decimal("0.05"),
    )
    assert warmup.side == SignalSide.HOLD
    assert warmup.reason_code == "strategy_history_warmup"

    flat = directional_signal(
        (Decimal("100"),) * 60,
        fee_pct=Decimal("0.1"),
        slippage_pct=Decimal("0.05"),
    )
    assert flat.side == SignalSide.HOLD
    assert flat.reason_code == "strategy_no_signal"


def test_directional_signal_can_buy_and_sell_without_future_samples() -> None:
    rising = tuple(Decimal(100 + index) for index in range(60))
    falling = tuple(reversed(rising))
    assert directional_signal(
        rising, fee_pct=Decimal("0.1"), slippage_pct=Decimal("0.05")
    ).side == SignalSide.BUY
    assert directional_signal(
        falling, fee_pct=Decimal("0.1"), slippage_pct=Decimal("0.05")
    ).side == SignalSide.SELL


def test_walk_forward_reports_costs_drawdown_and_buy_hold() -> None:
    prices = tuple(
        Decimal(value)
        for value in (
            list(range(100, 151))
            + list(range(150, 109, -1))
            + list(range(110, 171))
        )
    )
    report = walk_forward(
        prices,
        fee_pct=Decimal("0.1"),
        slippage_pct=Decimal("0.05"),
    )
    assert report.samples == len(prices)
    assert report.closed_trades >= 1
    assert Decimal(0) <= report.win_rate_pct <= Decimal(100)
    assert report.max_drawdown_pct >= 0
    assert report.buy_hold_return_pct != report.strategy_return_pct
