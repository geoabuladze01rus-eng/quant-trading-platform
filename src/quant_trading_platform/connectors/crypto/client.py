from decimal import Decimal
from time import time_ns
from typing import Protocol, cast

import httpx

from quant_trading_platform.config import Settings
from quant_trading_platform.market_data.models import NormalizedOrderBook, normalize_order_book
from quant_trading_platform.models import MarketQuote, Venue, normalize_symbol
from quant_trading_platform.safety import assert_live_order_allowed


class CryptoConnector(Protocol):
    venue: Venue

    def get_order_book(self, symbol: str) -> NormalizedOrderBook: ...
    def get_ticker(self, symbol: str) -> MarketQuote: ...
    def get_fees(self, symbol: str) -> dict[str, str]: ...
    def get_balances(self) -> list[dict[str, str]]: ...
    def place_order(self, symbol: str, side: str, quantity: Decimal) -> None: ...
    def close(self) -> None: ...


class MarketDataError(RuntimeError):
    """Sanitized API error: no raw response, URL or credentials in diagnostics."""


def _now_ms() -> int:
    return time_ns() // 1_000_000


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MarketDataError("Malformed public market data response")
    return cast(dict[str, object], value)


def _levels(value: object) -> list[list[str]]:
    if not isinstance(value, list):
        raise MarketDataError("Malformed public order book depth")
    levels: list[list[str]] = []
    for row in value:
        if not isinstance(row, list) or len(row) < 2:
            raise MarketDataError("Malformed public order book level")
        if not isinstance(row[0], str) or not isinstance(row[1], str):
            raise MarketDataError("Malformed public order book numbers")
        levels.append([row[0], row[1]])
    return levels


def _timestamp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise MarketDataError("Missing or invalid exchange timestamp")
    try:
        return int(value)
    except ValueError:
        raise MarketDataError("Missing or invalid exchange timestamp") from None


class PublicCryptoConnector:
    venue: Venue
    endpoint: str

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _snapshot(self, params: dict[str, str]) -> tuple[dict[str, object], int, int]:
        if self._client is None:
            self._client = httpx.Client(trust_env=False)
        # An independent request cannot inherit client authentication, cookies or headers.
        request = httpx.Request(
            "GET", self.endpoint, params=params,
            extensions={"timeout": httpx.Timeout(5.0).as_dict()},
        )
        started_at_ms = _now_ms()
        try:
            response = self._client.send(request, auth=None, follow_redirects=False)
            received_at_ms = _now_ms()
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise MarketDataError("Public market data request failed") from None
        return _object(payload), started_at_ms, received_at_ms

    def get_order_book(self, symbol: str) -> NormalizedOrderBook:
        raise NotImplementedError

    def get_ticker(self, symbol: str) -> MarketQuote:
        return self.get_order_book(symbol).to_quote()

    def get_fees(self, symbol: str) -> dict[str, str]:
        return {
            "symbol": normalize_symbol(symbol), "maker_pct": "0.10", "taker_pct": "0.10",
            "source": "paper_estimate", "note": "Not account-specific exchange fees",
        }

    def get_balances(self) -> list[dict[str, str]]:
        raise MarketDataError("Balances are unavailable through public read-only market data")

    def place_order(self, symbol: str, side: str, quantity: Decimal) -> None:
        assert_live_order_allowed(self.settings)
        raise NotImplementedError("Live order execution is intentionally not implemented")

    def _symbol(self, symbol: str) -> str:
        normalized = normalize_symbol(symbol)
        if "/" not in normalized:
            raise ValueError("Crypto market data requires a spot base/quote pair")
        return normalized


class BinanceConnector(PublicCryptoConnector):
    venue = Venue.BINANCE
    endpoint = "https://api.binance.com/api/v3/depth"

    def get_order_book(self, symbol: str) -> NormalizedOrderBook:
        symbol = self._symbol(symbol)
        data, started, received = self._snapshot(
            {"symbol": symbol.replace("/", ""), "limit": "100"}
        )
        # REST depth has lastUpdateId, not exchange time. Conservatively age from request start.
        return normalize_order_book(
            venue=self.venue, symbol=symbol, bids=_levels(data.get("bids")),
            asks=_levels(data.get("asks")), timestamp_ms=started, received_at_ms=received,
            max_age_ms=self.settings.max_market_data_age_ms, timestamp_source="request_start",
        )


class BybitConnector(PublicCryptoConnector):
    venue = Venue.BYBIT
    endpoint = "https://api.bybit.com/v5/market/orderbook"

    def get_order_book(self, symbol: str) -> NormalizedOrderBook:
        symbol = self._symbol(symbol)
        data, _, received = self._snapshot(
            {"category": "spot", "symbol": symbol.replace("/", ""), "limit": "50"}
        )
        if data.get("retCode") != 0:
            raise MarketDataError("Bybit public market data API returned an error")
        result = _object(data.get("result"))
        if result.get("s") != symbol.replace("/", ""):
            raise MarketDataError("Bybit public order book symbol mismatch")
        return normalize_order_book(
            venue=self.venue, symbol=symbol, bids=_levels(result.get("b")),
            asks=_levels(result.get("a")),
            timestamp_ms=_timestamp(result.get("cts", result.get("ts"))),
            received_at_ms=received, max_age_ms=self.settings.max_market_data_age_ms,
        )


class OKXConnector(PublicCryptoConnector):
    venue = Venue.OKX
    endpoint = "https://www.okx.com/api/v5/market/books"

    def get_order_book(self, symbol: str) -> NormalizedOrderBook:
        symbol = self._symbol(symbol)
        data, _, received = self._snapshot({"instId": symbol.replace("/", "-"), "sz": "100"})
        if data.get("code") != "0":
            raise MarketDataError("OKX public market data API returned an error")
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != 1:
            raise MarketDataError("OKX public order book snapshot is unavailable")
        result = _object(rows[0])
        return normalize_order_book(
            venue=self.venue, symbol=symbol, bids=_levels(result.get("bids")),
            asks=_levels(result.get("asks")), timestamp_ms=_timestamp(result.get("ts")),
            received_at_ms=received, max_age_ms=self.settings.max_market_data_age_ms,
        )
