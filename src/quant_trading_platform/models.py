from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Exchange(StrEnum):
    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"


@dataclass(frozen=True)
class MarketQuote:
    exchange: Exchange
    symbol: str
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    timestamp_ms: int


@dataclass(frozen=True)
class ArbitrageOpportunity:
    strategy: str
    symbol: str
    buy_exchange: Exchange
    sell_exchange: Exchange
    expected_gross_pct: Decimal
    expected_net_pct: Decimal
    max_notional_usd: Decimal
    detected_at_ms: int

    @property
    def is_profitable(self) -> bool:
        return self.expected_net_pct > Decimal("0")
