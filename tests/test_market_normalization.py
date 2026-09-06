from decimal import Decimal

import pytest

from quant_trading_platform.market_data.models import normalize_order_book
from quant_trading_platform.models import MarketType, Venue


def test_sorted_depth_and_quote_preserve_sizes_and_timestamp() -> None:
    book = normalize_order_book(
        Venue.BINANCE, " btcusdt ", [["99", "3"], ["100", "2"]],
        [["102", "4"], ["101", "5"]], 1000, 1100,
    )
    quote = book.to_quote()
    assert quote.symbol == "BTC/USDT"
    assert (quote.bid, quote.ask) == (Decimal("100"), Decimal("101"))
    assert (quote.bid_size, quote.ask_size) == (Decimal("2"), Decimal("5"))
    assert quote.timestamp_ms == 1000
    assert len(book.bids) == len(book.asks) == 2


@pytest.mark.parametrize("timestamp,received", [(0, 1001), (1001, 1000), (-1, 1000)])
def test_invalid_stale_future_timestamps_rejected(timestamp: int, received: int) -> None:
    with pytest.raises(ValueError):
        normalize_order_book(Venue.BYBIT, "BTC/USDT", [[100, 1]], [[101, 1]], timestamp, received)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "0", "bad"])
def test_invalid_price_or_size_rejected(bad: str) -> None:
    for bids in ([[bad, "1"]], [["100", bad]]):
        with pytest.raises(ValueError):
            normalize_order_book(Venue.OKX, "BTC-USDT", bids, [[101, 1]], 1000, 1000)


@pytest.mark.parametrize("bids", [[], [[100, 1], [100, 2]], [[102, 1]], [[100]]])
def test_invalid_depth_rejected(bids: list[list[int]]) -> None:
    with pytest.raises(ValueError):
        normalize_order_book(Venue.BINANCE, "BTCUSDT", bids, [[101, 1]], 1000, 1000)


@pytest.mark.parametrize("venue,market", [
    (Venue.BINANCE, MarketType.RUSSIAN_STOCKS), (Venue.T_INVEST, MarketType.CRYPTO),
])
def test_crypto_russian_stocks_mixing_blocked(venue: Venue, market: MarketType) -> None:
    with pytest.raises(ValueError, match="mismatch"):
        normalize_order_book(venue, "SBER", [[100, 1]], [[101, 1]], 1000, 1000, market_type=market)


def test_russian_stock_quote_keeps_separate_market() -> None:
    quote = normalize_order_book(
        Venue.T_INVEST, "sber", [[100, 1]], [[101, 1]], 1000, 1000,
        market_type=MarketType.RUSSIAN_STOCKS,
    ).to_quote()
    assert quote.market_type == MarketType.RUSSIAN_STOCKS
    assert quote.symbol == "SBER"
