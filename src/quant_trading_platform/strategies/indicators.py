"""Pure, deterministic indicators for read-only research. Financial values use Decimal.

Each series function returns a list aligned with its input: positions before the
indicator is defined hold None. No function here creates orders.
"""

from collections.abc import Sequence
from decimal import Decimal

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


def _check_period(period: int, length: int) -> None:
    if period < 1:
        raise ValueError("Indicator period must be at least 1")
    if length < period:
        raise ValueError("Not enough values for indicator period")


def sma(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    _check_period(period, len(values))
    result: list[Decimal | None] = [None] * (period - 1)
    window_sum = sum(values[:period], ZERO)
    result.append(window_sum / period)
    for index in range(period, len(values)):
        window_sum += values[index] - values[index - period]
        result.append(window_sum / period)
    return result


def ema(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """EMA seeded with the SMA of the first `period` values; alpha = 2 / (period + 1)."""
    _check_period(period, len(values))
    alpha = Decimal(2) / Decimal(period + 1)
    result: list[Decimal | None] = [None] * (period - 1)
    current = sum(values[:period], ZERO) / period
    result.append(current)
    for value in values[period:]:
        current = alpha * value + (ONE - alpha) * current
        result.append(current)
    return result


def rsi(closes: Sequence[Decimal], period: int = 14) -> list[Decimal | None]:
    """Wilder RSI. Flat markets (no gains and no losses) return 50 by convention."""
    _check_period(period, len(closes) - 1)
    result: list[Decimal | None] = [None] * period
    gains = [max(closes[i] - closes[i - 1], ZERO) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], ZERO) for i in range(1, len(closes))]
    avg_gain = sum(gains[:period], ZERO) / period
    avg_loss = sum(losses[:period], ZERO) / period
    result.append(_rsi_value(avg_gain, avg_loss))
    for index in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period
        result.append(_rsi_value(avg_gain, avg_loss))
    return result


def _rsi_value(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
    if avg_gain == ZERO and avg_loss == ZERO:
        return Decimal(50)
    if avg_loss == ZERO:
        return HUNDRED
    relative_strength = avg_gain / avg_loss
    return HUNDRED - HUNDRED / (ONE + relative_strength)


def atr(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    period: int = 14,
) -> list[Decimal | None]:
    """Wilder ATR over true range; the first value needs `period` true ranges."""
    if not len(highs) == len(lows) == len(closes):
        raise ValueError("High, low and close series must have equal length")
    _check_period(period, len(closes) - 1)
    true_ranges = [highs[0] - lows[0]]
    for index in range(1, len(closes)):
        previous_close = closes[index - 1]
        true_ranges.append(max(
            highs[index] - lows[index],
            abs(highs[index] - previous_close),
            abs(lows[index] - previous_close),
        ))
    result: list[Decimal | None] = [None] * (period - 1)
    current = sum(true_ranges[:period], ZERO) / period
    result.append(current)
    for true_range in true_ranges[period:]:
        current = (current * (period - 1) + true_range) / period
        result.append(current)
    return result


def vwap(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    volumes: Sequence[Decimal],
) -> Decimal:
    """Cumulative VWAP over the whole series using typical price (H + L + C) / 3."""
    if not len(highs) == len(lows) == len(closes) == len(volumes):
        raise ValueError("Price and volume series must have equal length")
    if not closes:
        raise ValueError("VWAP needs at least one candle")
    total_volume = sum(volumes, ZERO)
    if total_volume <= ZERO:
        raise ValueError("VWAP needs positive total volume")
    weighted = sum(
        ((high + low + close) / 3) * volume
        for high, low, close, volume in zip(highs, lows, closes, volumes, strict=True)
    )
    return weighted / total_volume
