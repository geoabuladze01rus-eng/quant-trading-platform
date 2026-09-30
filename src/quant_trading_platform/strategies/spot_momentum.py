"""Deterministic, read-only research signals for OKX spot markets.

Signals describe a target for further review. They never create a paper or live order.
Only completed UTC daily candles may be passed to these functions.
"""

from dataclasses import dataclass
from decimal import Decimal

DAY_MS = 86_400_000
SYMBOLS = ("BTC/USDT", "ETH/USDT", "LTC/USDT")


@dataclass(frozen=True)
class DailyCandle:
    timestamp_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class SpotSignal:
    strategy: str
    symbol: str
    action: str
    reason_code: str
    human_reason: str
    candle_timestamp_ms: int
    close: Decimal
    reference: Decimal


def validate_candles(candles: tuple[DailyCandle, ...], *, now_ms: int) -> None:
    if len(candles) < 101:
        raise ValueError("Insufficient completed daily candles")
    previous = -DAY_MS
    for candle in candles:
        if (
            type(candle.timestamp_ms) is not int
            or candle.timestamp_ms != previous + DAY_MS and previous != -DAY_MS
            or candle.timestamp_ms % DAY_MS != 0
            or any(not x.is_finite() for x in (
                candle.open, candle.high, candle.low, candle.close, candle.volume
            ))
            or min(candle.open, candle.high, candle.low, candle.close) <= 0
            or candle.volume < 0
            or candle.high < max(candle.open, candle.close)
            or candle.low > min(candle.open, candle.close)
            or candle.low > candle.high
        ):
            raise ValueError("Invalid or discontinuous completed daily candles")
        previous = candle.timestamp_ms
    age = now_ms - candles[-1].timestamp_ms
    if age < DAY_MS or age >= 2 * DAY_MS:
        raise ValueError("Latest completed daily candle is missing or in the future")


def trend_signal(symbol: str, candles: tuple[DailyCandle, ...]) -> SpotSignal:
    """20-day breakout above SMA100; 10-day low or SMA100 is an exit warning."""
    if symbol not in SYMBOLS:
        raise ValueError("Unsupported OKX USDT spot pair")
    current = candles[-1]
    average = sum((c.close for c in candles[-100:]), Decimal(0)) / 100
    if current.close < average or current.close < min(c.low for c in candles[-11:-1]):
        action, code, reason = "exit_review", "trend_exit", "Тренд ослаб: проверьте выход в USDT."
    elif current.close > average and current.close > max(c.high for c in candles[-21:-1]):
        action, code, reason = "entry_review", "trend_breakout", "Пробой 20 дней выше SMA100."
    else:
        action, code, reason = "no_new_entry", "trend_unconfirmed", "Нового входа нет."
    return SpotSignal("daily_trend", symbol, action, code, reason, current.timestamp_ms,
                      current.close, average)


def relative_strength_signals(
    series: dict[str, tuple[DailyCandle, ...]],
) -> list[SpotSignal]:
    """Select at most one positive 30/60-day leader; no short sales."""
    if set(series) != set(SYMBOLS):
        raise ValueError("All three OKX spot pairs are required")
    timestamps = {candles[-1].timestamp_ms for candles in series.values()}
    if len(timestamps) != 1:
        raise ValueError("Spot pairs do not share a completed candle date")
    scores: dict[str, Decimal] = {}
    for symbol, candles in series.items():
        price = candles[-1].close
        average = sum((c.close for c in candles[-100:]), Decimal(0)) / 100
        return_30 = price / candles[-31].close - 1
        return_60 = price / candles[-61].close - 1
        if price > average and return_30 > 0 and return_60 > 0:
            scores[symbol] = (return_30 + return_60) / 2
    leader = max(scores, key=lambda symbol: (scores[symbol], symbol)) if scores else None
    result = []
    for symbol in SYMBOLS:
        current = series[symbol][-1]
        if symbol == leader:
            action, code, reason = (
                "entry_review", "momentum_leader", "Лидер 30/60 дней; проверьте вход."
            )
        else:
            action, code, reason = (
                "no_new_entry", "momentum_not_selected", "Монета не выбрана; новых покупок нет."
            )
        result.append(SpotSignal(
            "relative_strength", symbol, action, code, reason, current.timestamp_ms,
            current.close, scores.get(symbol, Decimal(0)),
        ))
    return result
