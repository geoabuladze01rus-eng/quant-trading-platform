from decimal import Decimal, InvalidOperation
from time import time_ns
from typing import Protocol, cast

import httpx

from quant_trading_platform.config import Settings
from quant_trading_platform.crypto_universe import SpotInstrumentRules, crypto_spot_pair
from quant_trading_platform.market_data.models import NormalizedOrderBook, normalize_order_book
from quant_trading_platform.models import MarketQuote, Venue, normalize_symbol
from quant_trading_platform.safety import assert_live_order_allowed


class CryptoConnector(Protocol):
    venue: Venue

    def get_order_book(self, symbol: str) -> NormalizedOrderBook: ...
    def get_ticker(self, symbol: str) -> MarketQuote: ...
    def get_instrument_rules(self, symbol: str) -> SpotInstrumentRules: ...
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


def _positive_decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise MarketDataError(f"Missing or invalid {field}")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        raise MarketDataError(f"Missing or invalid {field}") from None
    if not parsed.is_finite() or parsed <= 0:
        raise MarketDataError(f"Missing or invalid {field}")
    return parsed


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

    def _snapshot(
        self, params: dict[str, str], *, endpoint: str | None = None
    ) -> tuple[dict[str, object], int, int]:
        if self._client is None:
            self._client = httpx.Client(trust_env=False)
        # An independent request cannot inherit client authentication, cookies or headers.
        request = httpx.Request(
            "GET", endpoint or self.endpoint, params=params,
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

    def get_instrument_rules(self, symbol: str) -> SpotInstrumentRules:
        raise NotImplementedError

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
    instrument_endpoint = "https://api.binance.com/api/v3/exchangeInfo"

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

    def get_instrument_rules(self, symbol: str) -> SpotInstrumentRules:
        symbol = self._symbol(symbol)
        pair = crypto_spot_pair(symbol)
        data, _, received = self._snapshot(
            {"symbol": symbol.replace("/", "")}, endpoint=self.instrument_endpoint
        )
        rows = data.get("symbols")
        if not isinstance(rows, list) or len(rows) != 1:
            raise MarketDataError("Binance spot instrument is unavailable")
        row = _object(rows[0])
        if (
            row.get("symbol") != symbol.replace("/", "")
            or row.get("baseAsset") != pair.base_asset
            or row.get("quoteAsset") != pair.quote_asset
        ):
            raise MarketDataError("Binance spot instrument identity mismatch")
        if row.get("status") != "TRADING" or row.get("isSpotTradingAllowed") is not True:
            raise MarketDataError("Binance spot instrument is unavailable")
        filters = row.get("filters")
        if not isinstance(filters, list):
            raise MarketDataError("Binance spot filters are unavailable")
        by_type = {
            str(item.get("filterType")): _object(item)
            for item in filters
            if isinstance(item, dict)
        }
        price, lot = by_type.get("PRICE_FILTER"), by_type.get("LOT_SIZE")
        notional = by_type.get("NOTIONAL") or by_type.get("MIN_NOTIONAL")
        if price is None or lot is None or notional is None:
            raise MarketDataError("Binance spot filters are incomplete")
        rules = SpotInstrumentRules(
            self.venue,
            symbol,
            pair.base_asset,
            pair.quote_asset,
            _positive_decimal(price.get("tickSize"), field="Binance tick size"),
            _positive_decimal(lot.get("stepSize"), field="Binance quantity step"),
            _positive_decimal(lot.get("minQty"), field="Binance minimum quantity"),
            _positive_decimal(notional.get("minNotional"), field="Binance minimum notional"),
            "trading",
            received,
            "binance:exchangeInfo",
        )
        rules.validate()
        return rules


class BybitConnector(PublicCryptoConnector):
    venue = Venue.BYBIT
    endpoint = "https://api.bybit.com/v5/market/orderbook"
    instrument_endpoint = "https://api.bybit.com/v5/market/instruments-info"

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

    def get_instrument_rules(self, symbol: str) -> SpotInstrumentRules:
        symbol = self._symbol(symbol)
        pair = crypto_spot_pair(symbol)
        data, _, received = self._snapshot(
            {"category": "spot", "symbol": symbol.replace("/", "")},
            endpoint=self.instrument_endpoint,
        )
        if data.get("retCode") != 0:
            raise MarketDataError("Bybit spot instrument API returned an error")
        result = _object(data.get("result"))
        rows = result.get("list")
        if not isinstance(rows, list) or len(rows) != 1:
            raise MarketDataError("Bybit spot instrument is unavailable")
        row = _object(rows[0])
        if (
            row.get("symbol") != symbol.replace("/", "")
            or row.get("baseCoin") != pair.base_asset
            or row.get("quoteCoin") != pair.quote_asset
        ):
            raise MarketDataError("Bybit spot instrument identity mismatch")
        if row.get("status") != "Trading":
            raise MarketDataError("Bybit spot instrument is unavailable")
        price = _object(row.get("priceFilter"))
        lot = _object(row.get("lotSizeFilter"))
        rules = SpotInstrumentRules(
            self.venue,
            symbol,
            pair.base_asset,
            pair.quote_asset,
            _positive_decimal(price.get("tickSize"), field="Bybit tick size"),
            _positive_decimal(lot.get("basePrecision"), field="Bybit quantity step"),
            _positive_decimal(
                lot.get("minOrderQty", lot.get("basePrecision")),
                field="Bybit minimum quantity",
            ),
            _positive_decimal(lot.get("minOrderAmt"), field="Bybit minimum notional"),
            "trading",
            received,
            "bybit:instruments-info",
        )
        rules.validate()
        return rules


class OKXConnector(PublicCryptoConnector):
    venue = Venue.OKX
    endpoint = "https://www.okx.com/api/v5/market/books"
    instrument_endpoint = "https://www.okx.com/api/v5/public/instruments"

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

    def get_instrument_rules(self, symbol: str) -> SpotInstrumentRules:
        symbol = self._symbol(symbol)
        pair = crypto_spot_pair(symbol)
        instrument_id = symbol.replace("/", "-")
        data, _, received = self._snapshot(
            {"instType": "SPOT", "instId": instrument_id}, endpoint=self.instrument_endpoint
        )
        if data.get("code") != "0":
            raise MarketDataError("OKX spot instrument API returned an error")
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != 1:
            raise MarketDataError("OKX spot instrument is unavailable")
        row = _object(rows[0])
        if (
            row.get("instId") != instrument_id
            or row.get("baseCcy") != pair.base_asset
            or row.get("quoteCcy") != pair.quote_asset
            or row.get("instType") != "SPOT"
        ):
            raise MarketDataError("OKX spot instrument identity mismatch")
        if row.get("state") != "live":
            raise MarketDataError("OKX spot instrument is unavailable")
        rules = SpotInstrumentRules(
            self.venue,
            symbol,
            pair.base_asset,
            pair.quote_asset,
            _positive_decimal(row.get("tickSz"), field="OKX tick size"),
            _positive_decimal(row.get("lotSz"), field="OKX quantity step"),
            _positive_decimal(row.get("minSz"), field="OKX minimum quantity"),
            None,
            "trading",
            received,
            "okx:public-instruments",
        )
        rules.validate()
        return rules
