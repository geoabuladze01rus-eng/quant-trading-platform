from decimal import Decimal

import pytest

from quant_trading_platform.market_data.derivatives import (
    derivatives_instrument_id,
    normalize_derivatives_snapshot,
)
from quant_trading_platform.models import Venue


def test_normalizes_derivatives_snapshot_with_decimal_fields() -> None:
    snapshot = normalize_derivatives_snapshot(
        venue=Venue.BINANCE,
        symbol=" btc-usdt ",
        instrument_id="BTCUSDT",
        timestamp_ms=9_950,
        received_at_ms=10_000,
        mark_price="100.5",
        index_price="100.0",
        funding_rate="-0.0001",
        next_funding_time_ms=20_000,
        open_interest="123.45",
        open_interest_unit="BTC",
        source_fields=("mark_price", "index_price", "funding_rate", "open_interest"),
        max_age_ms=1_000,
    )
    assert snapshot.symbol == "BTC/USDT"
    assert snapshot.instrument_id == "BTCUSDT"
    assert snapshot.mark_price == Decimal("100.5")
    assert snapshot.index_price == Decimal("100.0")
    assert snapshot.funding_rate == Decimal("-0.0001")
    assert snapshot.open_interest == Decimal("123.45")
    assert snapshot.open_interest_unit == "BTC"
    assert snapshot.source_fields == (
        "mark_price",
        "index_price",
        "funding_rate",
        "open_interest",
    )


@pytest.mark.parametrize(
    ("venue", "symbol", "expected"),
    [
        (Venue.BINANCE, "BTC/USDT", "BTCUSDT"),
        (Venue.BYBIT, "ETHUSDT", "ETHUSDT"),
        (Venue.OKX, "SOL-USDT", "SOL-USDT-SWAP"),
    ],
)
def test_maps_canonical_symbol_to_derivatives_instrument(
    venue: Venue, symbol: str, expected: str,
) -> None:
    assert derivatives_instrument_id(venue, symbol) == expected


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0"])
def test_rejects_nonpositive_or_nonfinite_prices(value: str) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        normalize_derivatives_snapshot(
            venue=Venue.BINANCE,
            symbol="BTC/USDT",
            instrument_id="BTCUSDT",
            timestamp_ms=9_950,
            received_at_ms=10_000,
            mark_price=value,
            index_price="100",
            funding_rate="0",
            next_funding_time_ms=None,
            open_interest="1",
            open_interest_unit="BTC",
            source_fields=("mark_price",),
            max_age_ms=1_000,
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0"])
def test_rejects_nonpositive_or_nonfinite_open_interest(value: str) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        normalize_derivatives_snapshot(
            venue=Venue.BINANCE,
            symbol="BTC/USDT",
            instrument_id="BTCUSDT",
            timestamp_ms=9_950,
            received_at_ms=10_000,
            mark_price="100",
            index_price="100",
            funding_rate="0",
            next_funding_time_ms=None,
            open_interest=value,
            open_interest_unit="BTC",
            source_fields=("open_interest",),
            max_age_ms=1_000,
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_rejects_nonfinite_funding_rate(value: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        normalize_derivatives_snapshot(
            venue=Venue.BINANCE,
            symbol="BTC/USDT",
            instrument_id="BTCUSDT",
            timestamp_ms=9_950,
            received_at_ms=10_000,
            mark_price="100",
            index_price="100",
            funding_rate=value,
            next_funding_time_ms=None,
            open_interest="1",
            open_interest_unit="BTC",
            source_fields=("funding_rate",),
            max_age_ms=1_000,
        )


def test_keeps_optional_fields_missing_instead_of_inventing_values() -> None:
    snapshot = normalize_derivatives_snapshot(
        venue=Venue.BYBIT,
        symbol="ETH/USDT",
        instrument_id="ETHUSDT",
        timestamp_ms=9_950,
        received_at_ms=10_000,
        mark_price="2000",
        index_price=None,
        funding_rate=None,
        next_funding_time_ms=None,
        open_interest=None,
        open_interest_unit=None,
        source_fields=("mark_price",),
        max_age_ms=1_000,
    )
    assert snapshot.index_price is None
    assert snapshot.funding_rate is None
    assert snapshot.open_interest is None
    assert snapshot.open_interest_unit is None


def test_rejects_wrong_instrument_identity() -> None:
    with pytest.raises(ValueError, match="instrument identity"):
        normalize_derivatives_snapshot(
            venue=Venue.BINANCE,
            symbol="BTC/USDT",
            instrument_id="ETHUSDT",
            timestamp_ms=9_950,
            received_at_ms=10_000,
            mark_price="100",
            index_price="100",
            funding_rate="0",
            next_funding_time_ms=None,
            open_interest="1",
            open_interest_unit="BTC",
            source_fields=("mark_price",),
            max_age_ms=1_000,
        )


@pytest.mark.parametrize(
    ("timestamp_ms", "received_at_ms", "match"),
    [(10_001, 10_000, "Future"), (8_000, 10_000, "Stale")],
)
def test_rejects_future_and_stale_source_timestamps(
    timestamp_ms: int, received_at_ms: int, match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        normalize_derivatives_snapshot(
            venue=Venue.OKX,
            symbol="SOL/USDT",
            instrument_id="SOL-USDT-SWAP",
            timestamp_ms=timestamp_ms,
            received_at_ms=received_at_ms,
            mark_price="100",
            index_price="100",
            funding_rate="0",
            next_funding_time_ms=None,
            open_interest="1",
            open_interest_unit="contracts",
            source_fields=("mark_price",),
            max_age_ms=1_000,
        )
