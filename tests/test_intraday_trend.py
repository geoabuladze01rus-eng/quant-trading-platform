from decimal import Decimal

import pytest

from quant_trading_platform.strategies.intraday_trend import intraday_candidate
from quant_trading_platform.strategies.spot_momentum import DailyCandle

H1 = 3_600_000
H4 = 4 * H1


def series(
    closes: list[Decimal], interval_ms: int, volume_last: Decimal = Decimal(1),
) -> tuple[DailyCandle, ...]:
    end = 10_000 * interval_ms
    rows = []
    for index, close in enumerate(closes):
        volume = volume_last if index == len(closes) - 1 else Decimal(1)
        rows.append(DailyCandle(
            end - (len(closes) - 1 - index) * interval_ms,
            close, close + Decimal("0.5"), close - Decimal("0.5"), close, volume,
        ))
    return tuple(rows)


def up_4h() -> tuple[DailyCandle, ...]:
    return series([Decimal(100) + Decimal("0.5") * i for i in range(80)], H4)


def down_4h() -> tuple[DailyCandle, ...]:
    return series([Decimal(200) - Decimal("0.5") * i for i in range(80)], H4)


def rising_1h_with_breakout() -> tuple[DailyCandle, ...]:
    closes = [Decimal(100) + Decimal("0.1") * i for i in range(120)]
    closes[-1] = Decimal(117)  # clear close above the prior 20-candle highs
    return series(closes, H1, volume_last=Decimal(10))


def test_confirmed_breakout_in_uptrend_is_entry_review_with_stop() -> None:
    candidate = intraday_candidate("BTC/USDT", up_4h(), rising_1h_with_breakout())
    assert candidate.action == "entry_review"
    assert candidate.reason_code == "intraday_breakout_confirmed"
    assert len(candidate.confirmations) == 5
    assert candidate.stop_reference is not None
    assert Decimal(0) < candidate.stop_reference < candidate.close


def test_downtrend_on_4h_is_exit_review_without_stop() -> None:
    candidate = intraday_candidate("ETH/USDT", down_4h(), rising_1h_with_breakout())
    assert candidate.action == "exit_review"
    assert candidate.stop_reference is None


def test_no_breakout_in_uptrend_is_no_new_entry() -> None:
    closes = [Decimal(100) + Decimal("0.1") * i for i in range(120)]
    closes[-1] = closes[-2]  # flat last close stays below the prior highs
    candidate = intraday_candidate("BTC/USDT", up_4h(), series(closes, H1))
    assert candidate.action == "no_new_entry"
    assert candidate.reason_code == "intraday_unconfirmed"
    assert candidate.stop_reference is None


def test_insufficient_candles_fail_closed() -> None:
    with pytest.raises(ValueError, match="Not enough"):
        intraday_candidate("BTC/USDT", up_4h()[:10], rising_1h_with_breakout())
