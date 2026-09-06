"""Validated spot snapshots shared by public read-only venue adapters."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from quant_trading_platform.models import (
    MarketQuote,
    MarketType,
    Venue,
    normalize_symbol,
    venue_matches_market,
)


class StaleMarketDataError(ValueError):
    code = "stale"


@dataclass(frozen=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class NormalizedOrderBook:
    venue: Venue
    symbol: str
    market_type: MarketType
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    timestamp_ms: int
    received_at_ms: int
    timestamp_source: str = "exchange"

    def to_quote(self) -> MarketQuote:
        quote = MarketQuote(
            venue=self.venue, symbol=self.symbol, market_type=self.market_type,
            bid=self.bids[0].price, ask=self.asks[0].price,
            bid_size=self.bids[0].quantity, ask_size=self.asks[0].quantity,
            timestamp_ms=self.timestamp_ms,
        )
        quote.validate()
        return quote


def _levels(raw: Sequence[Sequence[object]], *, descending: bool) -> tuple[OrderBookLevel, ...]:
    if not raw or len(raw) > 5000:
        raise ValueError("Order book depth must contain 1 to 5000 levels per side")
    levels: list[OrderBookLevel] = []
    prices: set[Decimal] = set()
    for row in raw:
        if isinstance(row, (str, bytes)) or len(row) < 2:
            raise ValueError("Order book level requires price and quantity")
        try:
            price, quantity = Decimal(str(row[0])), Decimal(str(row[1]))
        except InvalidOperation as error:
            raise ValueError("Invalid order book number") from error
        if not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity <= 0:
            raise ValueError("Order book prices and sizes must be finite and positive")
        if price in prices:
            raise ValueError("Duplicate order book price")
        prices.add(price)
        levels.append(OrderBookLevel(price, quantity))
    return tuple(sorted(levels, key=lambda level: level.price, reverse=descending))


def normalize_order_book(
    venue: Venue,
    symbol: str,
    bids: Sequence[Sequence[object]],
    asks: Sequence[Sequence[object]],
    timestamp_ms: int,
    received_at_ms: int,
    max_age_ms: int = 1000,
    timestamp_source: str = "exchange",
    market_type: MarketType = MarketType.CRYPTO,
) -> NormalizedOrderBook:
    """Normalize complete snapshots; deltas and missing source time are not guessed.

    Receipt timestamps are allowed only when the public API omits exchange time;
    callers must expose this provenance because upstream freshness is then unknown.
    """
    if not venue_matches_market(venue, market_type):
        raise ValueError("Venue and market_type mismatch")
    normalized_symbol = normalize_symbol(symbol)
    if market_type == MarketType.CRYPTO and "/" not in normalized_symbol:
        raise ValueError("Crypto order book requires a spot pair")
    if market_type == MarketType.RUSSIAN_STOCKS and "/" in normalized_symbol:
        raise ValueError("Russian stocks require an instrument ticker")
    if any(type(value) is not int or value < 0 for value in (timestamp_ms, received_at_ms)):
        raise ValueError("Invalid timestamp")
    if type(max_age_ms) is not int or max_age_ms <= 0:
        raise ValueError("max_age_ms must be positive")
    if timestamp_source not in ("exchange", "receipt", "request_start"):
        raise ValueError("Invalid timestamp source")
    if timestamp_ms > received_at_ms:
        raise ValueError("Future market data timestamp")
    if received_at_ms - timestamp_ms > max_age_ms:
        raise StaleMarketDataError("Stale market data")
    sorted_bids, sorted_asks = _levels(bids, descending=True), _levels(asks, descending=False)
    if sorted_bids[0].price > sorted_asks[0].price:
        raise ValueError("Crossed order book")
    return NormalizedOrderBook(
        venue, normalized_symbol, market_type, sorted_bids, sorted_asks,
        timestamp_ms, received_at_ms, timestamp_source,
    )
