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

    @property
    def is_profitable(self) -> bool:
        return self.expected_net_pct > Decimal("0")
