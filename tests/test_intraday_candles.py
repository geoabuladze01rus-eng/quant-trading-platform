from decimal import Decimal

import pytest

from quant_trading_platform.strategies.spot_momentum import (
    BAR_MS,
    DAY_MS,
    DailyCandle,
    validate_candles,
)

HOUR_MS = 3_600_000


def make_candles(end_ts: int, interval_ms: int, count: int = 130) -> tuple[DailyCandle, ...]:
    return tuple(
        DailyCandle(
            end_ts - (count - 1 - index) * interval_ms,
            Decimal(100 + index), Decimal(101 + index), Decimal(99 + index),
            Decimal(100 + index), Decimal(1),
        )
        for index in range(count)
    )


def last_closed_bar_ms(now_ms: int, interval_ms: int) -> int:
    return (now_ms // interval_ms - 1) * interval_ms


def test_supported_bars_have_expected_lengths() -> None:
    assert BAR_MS["1Dutc"] == DAY_MS
    assert BAR_MS["4Hutc"] == 4 * HOUR_MS
    assert BAR_MS["1Hutc"] == HOUR_MS
    assert BAR_MS["15m"] == 15 * 60_000
    assert BAR_MS["5m"] == 5 * 60_000
    assert "1m" not in BAR_MS


def test_four_hour_candles_validate_with_four_hour_interval() -> None:
    now_ms = 1_000 * 4 * HOUR_MS + 30 * 60_000
    end = last_closed_bar_ms(now_ms, 4 * HOUR_MS)
    validate_candles(make_candles(end, 4 * HOUR_MS), now_ms=now_ms, interval_ms=4 * HOUR_MS)


def test_daily_spacing_is_rejected_for_four_hour_interval() -> None:
    now_ms = 1_000 * 4 * HOUR_MS + 30 * 60_000
    end = last_closed_bar_ms(now_ms, 4 * HOUR_MS)
    with pytest.raises(ValueError, match="discontinuous"):
        validate_candles(make_candles(end, DAY_MS), now_ms=now_ms, interval_ms=4 * HOUR_MS)


def test_default_interval_is_still_daily() -> None:
    now_ms = 1_000 * DAY_MS + HOUR_MS
    end = last_closed_bar_ms(now_ms, DAY_MS)
    validate_candles(make_candles(end, DAY_MS), now_ms=now_ms)


def test_stale_four_hour_series_fails_closed() -> None:
    now_ms = 1_000 * 4 * HOUR_MS + 30 * 60_000
    end = last_closed_bar_ms(now_ms, 4 * HOUR_MS)
    # Two full intervals after the last closed bar is stale for a 4H series.
    with pytest.raises(ValueError, match="missing"):
        validate_candles(make_candles(end, 4 * HOUR_MS), now_ms=end + 2 * 4 * HOUR_MS,
                         interval_ms=4 * HOUR_MS)
