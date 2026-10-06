"""Reject untrusted financial values and identities before exposing evidence."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from quant_trading_platform.market_data.derivatives import normalize_derivatives_snapshot
from quant_trading_platform.models import Venue


def snapshot(**changes):
    values = dict(
        venue=Venue.BINANCE,
        symbol="btcusdt",
        instrument_id="BTCUSDT",
        timestamp_ms=1000,
        received_at_ms=1001,
    )
    values.update(changes)
    return normalize_derivatives_snapshot(**values)


def test_normalization_preserves_identity_and_missing_fields():
    result = snapshot(mark_price="123.4567890123456789", funding_rate="-0.001")
    assert result.symbol == "BTC/USDT"
    assert result.instrument_id == "BTCUSDT"
    assert result.mark_price == Decimal("123.4567890123456789")
    assert result.funding_rate == Decimal("-0.001")
    assert result.index_price is None
    assert result.open_interest is None
    with pytest.raises(FrozenInstanceError):
        result.symbol = "ETH/USDT"


@pytest.mark.parametrize("field", ["mark_price", "index_price", "open_interest"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", 1.1, True])
def test_invalid_financial_values_rejected(field, value):
    with pytest.raises(ValueError):
        snapshot(**{field: value})


@pytest.mark.parametrize("value", ["NaN", "Infinity", 0.1, True])
def test_funding_must_be_finite_exact_decimal(value):
    with pytest.raises(ValueError):
        snapshot(funding_rate=value)


@pytest.mark.parametrize(
    "changes",
    [
        {"instrument_id": "ETHUSDT"},
        {"symbol": "LTC/USDT"},
        {"venue": Venue.T_INVEST},
        {"timestamp_ms": 1002},
        {"timestamp_ms": 0},
        {"received_at_ms": True},
        {"open_interest": "1"},
        {"next_funding_time_ms": -1},
    ],
)
def test_untrusted_identity_timestamp_and_unit_rejected(changes):
    with pytest.raises(ValueError):
        snapshot(**changes)


@pytest.mark.parametrize(
    "venue,instrument",
    [
        (Venue.BINANCE, "SOLUSDT"),
        (Venue.BYBIT, "SOLUSDT"),
        (Venue.OKX, "SOL-USDT-SWAP"),
    ],
)
def test_instrument_mapping_and_native_units(venue, instrument):
    result = snapshot(
        venue=venue,
        symbol="SOL/USDT",
        instrument_id=instrument,
        open_interest="2",
        open_interest_unit="contracts",
        source_fields=("openInterest",),
    )
    assert result.open_interest == Decimal("2")
    assert result.open_interest_unit == "contracts"
    assert result.source_fields == ("openInterest",)
