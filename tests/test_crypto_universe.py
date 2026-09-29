from decimal import Decimal

import pytest

from quant_trading_platform.crypto_universe import (
    SUPPORTED_CRYPTO_BASE_ASSETS,
    SUPPORTED_CRYPTO_SPOT_SYMBOLS,
    SpotInstrumentRules,
    crypto_spot_pair,
    size_paper_spread,
)
from quant_trading_platform.models import Venue


def test_crypto_universe_is_explicit_usdt_spot_only() -> None:
    assert SUPPORTED_CRYPTO_SPOT_SYMBOLS == ("BTC/USDT", "ETH/USDT", "LTC/USDT")
    assert SUPPORTED_CRYPTO_BASE_ASSETS == ("BTC", "ETH", "LTC")
    assert crypto_spot_pair("ltcusdt").base_asset == "LTC"
    assert crypto_spot_pair("LTC-USDT").quote_asset == "USDT"


@pytest.mark.parametrize("symbol", ["BTC/USD", "ETH/USDC", "LTC/BTC", "DOGE/USDT"])
def test_crypto_universe_never_converts_or_guesses_quote_currency(symbol: str) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        crypto_spot_pair(symbol)


def rules(
    venue: Venue,
    *,
    tick: str = "0.01",
    step: str = "0.001",
    minimum: str = "0.001",
    notional: str | None = "5",
) -> SpotInstrumentRules:
    return SpotInstrumentRules(
        venue,
        "LTC/USDT",
        "LTC",
        "USDT",
        Decimal(tick),
        Decimal(step),
        Decimal(minimum),
        None if notional is None else Decimal(notional),
        "trading",
        1,
        "test:public",
    )


def test_spread_sizing_applies_both_venues_steps_and_minimums() -> None:
    result = size_paper_spread(
        Decimal("10"),
        buy_price=Decimal("100.00"),
        sell_price=Decimal("101.00"),
        buy_available=Decimal("1"),
        sell_available=Decimal("1"),
        buy_rules=rules(Venue.BYBIT, step="0.00001"),
        sell_rules=rules(Venue.OKX, step="0.000001", notional=None),
    )
    assert result.approved is True
    assert result.quantity == Decimal("0.1")
    assert result.buy_notional == Decimal("10")
    assert result.sell_notional == Decimal("10.1")
    assert result.common_quantity_step == Decimal("0.00001")


def test_spread_sizing_rejects_minimum_notional_and_price_step() -> None:
    too_small = size_paper_spread(
        Decimal("4"),
        buy_price=Decimal("100"),
        sell_price=Decimal("101"),
        buy_available=Decimal("1"),
        sell_available=Decimal("1"),
        buy_rules=rules(Venue.BYBIT),
        sell_rules=rules(Venue.OKX),
    )
    assert too_small.approved is False
    assert too_small.reason_code == "instrument_min_notional"

    invalid_tick = size_paper_spread(
        Decimal("10"),
        buy_price=Decimal("100.001"),
        sell_price=Decimal("101"),
        buy_available=Decimal("1"),
        sell_available=Decimal("1"),
        buy_rules=rules(Venue.BYBIT),
        sell_rules=rules(Venue.OKX),
    )
    assert invalid_tick.approved is False
    assert invalid_tick.reason_code == "instrument_price_step_mismatch"
