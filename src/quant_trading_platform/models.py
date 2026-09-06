from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class MarketType(StrEnum):
    CRYPTO = "crypto"
    RUSSIAN_STOCKS = "russian_stocks"


class Venue(StrEnum):
    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"
    T_INVEST = "t_invest"


Exchange = Venue


def normalize_symbol(symbol: str) -> str:
    """Normalize spot pair notation without guessing an unknown quote currency."""
    normalized = symbol.strip().upper().replace("-", "/").replace("_", "/")
    parts = normalized.split("/")
    if not all(part and part.isascii() and part.isalnum() for part in parts):
        raise ValueError("Invalid symbol")
    if len(parts) > 2:
        raise ValueError("Invalid symbol: expected a spot pair or instrument ticker")
    if len(parts) == 2:
        if parts[0] == parts[1]:
            raise ValueError("Symbol base and quote must differ")
        return normalized
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "EUR", "RUB"):
        if normalized.endswith(quote) and len(normalized) > len(quote):
            return normalize_symbol(f"{normalized[:-len(quote)]}/{quote}")
    return normalized


def venue_matches_market(venue: Venue, market_type: MarketType) -> bool:
    if market_type == MarketType.CRYPTO:
        return venue in (Venue.BINANCE, Venue.BYBIT, Venue.OKX)
    return market_type == MarketType.RUSSIAN_STOCKS and venue == Venue.T_INVEST


@dataclass(frozen=True)
class MarketQuote:
    venue: Venue
    symbol: str
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    timestamp_ms: int
    market_type: MarketType = MarketType.CRYPTO

    def validate(self) -> None:
        normalize_symbol(self.symbol)
        if not venue_matches_market(self.venue, self.market_type):
            raise ValueError("Venue and market_type mismatch")
        for value in (self.bid, self.ask, self.bid_size, self.ask_size):
            if not value.is_finite() or value <= 0:
                raise ValueError("Quote prices and sizes must be finite and positive")
        if self.bid > self.ask:
            raise ValueError("Crossed order book")
        if self.timestamp_ms < 0:
            raise ValueError("Invalid quote timestamp")


@dataclass(frozen=True)
class ArbitrageOpportunity:
    strategy: str
    symbol: str
    buy_exchange: Venue
    sell_exchange: Venue
    expected_gross_pct: Decimal
    expected_net_pct: Decimal
    max_notional_usd: Decimal
    detected_at_ms: int
    gross_spread_pct: Decimal = Decimal("0")
    fees_pct: Decimal = Decimal("0")
    slippage_pct: Decimal = Decimal("0")
    rejection_reason: str | None = None
    market_type: MarketType = MarketType.CRYPTO
    source_timestamp_ms: int | None = None

    @property
    def is_profitable(self) -> bool:
        return self.expected_net_pct > Decimal("0")
