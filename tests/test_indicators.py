from decimal import Decimal

import pytest

from quant_trading_platform.strategies.indicators import atr, ema, rsi, sma, vwap


def D(*values: str) -> list[Decimal]:
    return [Decimal(value) for value in values]


def test_sma_is_aligned_and_exact() -> None:
    assert sma(D("1", "2", "3", "4", "5"), 3) == [None, None, Decimal(2), Decimal(3), Decimal(4)]


def test_ema_seeds_with_sma_and_applies_alpha() -> None:
    values = D("1", "2", "3", "4", "5")
    result = ema(values, 3)
    assert result[:2] == [None, None]
    assert result[2] == Decimal(2)  # seed = SMA of first 3
    alpha = Decimal(2) / Decimal(4)
    assert result[3] == alpha * Decimal(4) + (1 - alpha) * Decimal(2)


def test_rsi_is_100_when_only_gains() -> None:
    result = rsi(D("1", "2", "3", "4", "5", "6"), 3)
    assert result[3] == Decimal(100)
    assert result[-1] == Decimal(100)


def test_rsi_is_0_when_only_losses() -> None:
    result = rsi(D("6", "5", "4", "3", "2", "1"), 3)
    assert result[3] == Decimal(0)


def test_rsi_flat_market_is_50() -> None:
    result = rsi(D("7", "7", "7", "7", "7"), 3)
    assert result[3] == Decimal(50)


def test_rsi_length_matches_input() -> None:
    closes = D("10", "11", "10", "12", "11", "13", "12", "14")
    result = rsi(closes, 3)
    assert len(result) == len(closes)
    assert all(value is not None and Decimal(0) <= value <= Decimal(100)
               for value in result[3:])


def test_atr_on_constant_range_equals_range() -> None:
    highs = D("12", "12", "12", "12")
    lows = D("10", "10", "10", "10")
    closes = D("11", "11", "11", "11")
    result = atr(highs, lows, closes, 3)
    assert result[:2] == [None, None]
    assert result[2] == Decimal(2)
    assert result[3] == Decimal(2)


def test_atr_true_range_uses_previous_close_gap() -> None:
    highs = D("10", "20")
    lows = D("9", "18")
    closes = D("9.5", "19")
    # Period 1: second true range = max(20-18, |20-9.5|, |18-9.5|) = 10.5
    result = atr(highs, lows, closes, 1)
    assert result[1] == Decimal("10.5")


def test_vwap_weights_typical_price_by_volume() -> None:
    highs = D("11", "21")
    lows = D("9", "19")
    closes = D("10", "20")
    volumes = D("1", "3")
    # typical: 10 and 20; weighted: (10*1 + 20*3) / 4 = 17.5
    assert vwap(highs, lows, closes, volumes) == Decimal("17.5")


def test_vwap_rejects_zero_volume() -> None:
    with pytest.raises(ValueError):
        vwap(D("1"), D("1"), D("1"), D("0"))


@pytest.mark.parametrize("period", [0, -1])
def test_invalid_period_rejected(period: int) -> None:
    with pytest.raises(ValueError):
        sma(D("1", "2"), period)


def test_not_enough_values_rejected() -> None:
    with pytest.raises(ValueError):
        ema(D("1", "2"), 5)
