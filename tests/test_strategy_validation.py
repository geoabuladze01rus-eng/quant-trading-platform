from dataclasses import replace
from decimal import Decimal
from time import time

import pytest

from quant_trading_platform.models import MarketQuote, MarketType, Venue, normalize_symbol
from quant_trading_platform.strategies.arbitrage import (
    CrossVenueSpreadMonitor,
    TriangularArbitrageDetector,
)


def quote(venue: Venue = Venue.BINANCE, symbol: str = "BTC/USDT") -> MarketQuote:
    return MarketQuote(
        venue, symbol, Decimal("99"), Decimal("100"), Decimal("1"), Decimal("1"),
        int(time() * 1000),
    )


@pytest.mark.parametrize("symbol", ["btc/usdt", "BTC-USDT", " btc_usdt ", "BTCUSDT"])
def test_normalize_symbol_variants(symbol: str) -> None:
    assert normalize_symbol(symbol) == "BTC/USDT"


@pytest.mark.parametrize("symbol", ["", "BTC//USDT", "BTC/BTC", "BTC USDT", "BTC/USDT/SWAP"])
def test_ambiguous_or_invalid_symbol_rejected(symbol: str) -> None:
    with pytest.raises(ValueError):
        normalize_symbol(symbol)


@pytest.mark.parametrize("detector", [CrossVenueSpreadMonitor(), TriangularArbitrageDetector()])
def test_symbol_mismatch_blocked(
    detector: CrossVenueSpreadMonitor | TriangularArbitrageDetector,
) -> None:
    with pytest.raises(ValueError, match="Symbol mismatch"):
        detector.detect(quote(), quote(Venue.BYBIT, "ETH/USDT"), Decimal("0"), Decimal("0"))


def test_normalized_legs_keep_oldest_timestamp_and_costs() -> None:
    buy = quote(symbol="BTCUSDT")
    sell = replace(quote(Venue.OKX, "btc-usdt"), bid=Decimal("102"), ask=Decimal("103"))
    opportunity = CrossVenueSpreadMonitor().detect(
        replace(buy, timestamp_ms=buy.timestamp_ms - 2_000), sell,
        Decimal("0.20"), Decimal("0.30"),
    )
    assert opportunity.symbol == "BTC/USDT"
    assert opportunity.source_timestamp_ms == buy.timestamp_ms - 2_000
    assert opportunity.expected_gross_pct == Decimal("2")
    assert opportunity.expected_net_pct == Decimal("1.50")
    assert opportunity.max_notional_usd == Decimal("100")


@pytest.mark.parametrize("market_type", [MarketType.CRYPTO, MarketType.RUSSIAN_STOCKS])
def test_t_invest_cannot_mix_into_crypto(market_type: MarketType) -> None:
    with pytest.raises(ValueError, match="mismatch"):
        CrossVenueSpreadMonitor().detect(
            quote(), replace(quote(Venue.T_INVEST), market_type=market_type),
            Decimal("0"), Decimal("0"),
        )


def test_russian_market_not_evaluated_by_crypto_detector() -> None:
    russian = replace(quote(Venue.T_INVEST, "SBER"), market_type=MarketType.RUSSIAN_STOCKS)
    with pytest.raises(ValueError, match="Russian market"):
        CrossVenueSpreadMonitor().detect(russian, russian, Decimal("0"), Decimal("0"))


@pytest.mark.parametrize("cost", ["-0.1", "NaN", "Infinity"])
@pytest.mark.parametrize("is_fee", [True, False])
def test_invalid_costs_rejected(cost: str, is_fee: bool) -> None:
    fees, slippage = (Decimal(cost), Decimal("0")) if is_fee else (Decimal("0"), Decimal(cost))
    with pytest.raises(ValueError, match="Fees and slippage"):
        CrossVenueSpreadMonitor().detect(quote(), quote(Venue.OKX), fees, slippage)


@pytest.mark.parametrize("field", ["bid", "ask", "bid_size", "ask_size"])
@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity"])
def test_invalid_quote_values_rejected(field: str, value: str) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        CrossVenueSpreadMonitor().detect(
            replace(quote(), **{field: Decimal(value)}), quote(Venue.OKX),
            Decimal("0"), Decimal("0"),
        )


def test_crossed_book_rejected() -> None:
    with pytest.raises(ValueError, match="Crossed"):
        CrossVenueSpreadMonitor().detect(
            replace(quote(), bid=Decimal("101")), quote(Venue.OKX), Decimal("0"), Decimal("0"),
        )
