from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from quant_trading_platform.config import Settings
from quant_trading_platform.models import Venue
from quant_trading_platform.safety import assert_live_order_allowed


class CryptoConnector(Protocol):
    venue: Venue

    def get_order_book(self, symbol: str) -> dict[str, str]: ...
    def get_ticker(self, symbol: str) -> dict[str, str]: ...
    def get_fees(self, symbol: str) -> dict[str, str]: ...
    def get_balances(self) -> list[dict[str, str]]: ...
    def place_order(self, symbol: str, side: str, quantity: Decimal) -> None: ...


@dataclass(frozen=True)
class MockCryptoConnector:
    settings: Settings
    venue: Venue

    def get_order_book(self, symbol: str) -> dict[str, str]:
        return {"symbol": symbol, "bid": "100.00", "ask": "100.10", "venue": self.venue.value}

    def get_ticker(self, symbol: str) -> dict[str, str]:
        return {"symbol": symbol, "last": "100.05", "venue": self.venue.value}

    def get_fees(self, symbol: str) -> dict[str, str]:
        return {"symbol": symbol, "maker_pct": "0.05", "taker_pct": "0.10"}

    def get_balances(self) -> list[dict[str, str]]:
        return [{"asset": "USDT", "available": "10000", "venue": self.venue.value}]

    def place_order(self, symbol: str, side: str, quantity: Decimal) -> None:
        assert_live_order_allowed(self.settings)
        raise NotImplementedError("Live order execution is intentionally not implemented")


class BinanceConnector(MockCryptoConnector):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, Venue.BINANCE)


class BybitConnector(MockCryptoConnector):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, Venue.BYBIT)


class OKXConnector(MockCryptoConnector):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, Venue.OKX)
