"""Credential-free public derivatives market-data adapters."""

from time import time_ns
from typing import cast

import httpx

from quant_trading_platform.config import Settings
from quant_trading_platform.connectors.crypto.client import MarketDataError
from quant_trading_platform.market_data.derivatives import (
    DerivativesSnapshot,
    derivatives_instrument_id,
    normalize_derivatives_snapshot,
)
from quant_trading_platform.models import Venue, normalize_symbol


def _now_ms() -> int:
    return time_ns() // 1_000_000


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MarketDataError("Malformed public derivatives response")
    return cast(dict[str, object], value)


def _single_object(value: object, *, message: str) -> dict[str, object]:
    if not isinstance(value, list) or len(value) != 1:
        raise MarketDataError(message)
    return _object(value[0])


def _timestamp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise MarketDataError("Missing or invalid derivatives timestamp")
    try:
        return int(value)
    except ValueError:
        raise MarketDataError("Missing or invalid derivatives timestamp") from None


def _optional_timestamp(value: object) -> int | None:
    if value in (None, ""):
        return None
    return _timestamp(value)


def _decimal_value(value: object) -> str | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise MarketDataError("Missing or invalid derivatives numeric field")
    return value


class PublicDerivativesSource:
    venue: Venue

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _get(self, endpoint: str, params: dict[str, str]) -> tuple[dict[str, object], int]:
        if self._client is None:
            self._client = httpx.Client(trust_env=False)
        request = httpx.Request(
            "GET",
            endpoint,
            params=params,
            extensions={"timeout": httpx.Timeout(5.0).as_dict()},
        )
        try:
            response = self._client.send(request, auth=None, follow_redirects=False)
            received_at_ms = _now_ms()
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise MarketDataError("Public derivatives request failed") from None
        return _object(payload), received_at_ms

    def _canonical(self, symbol: str) -> tuple[str, str]:
        canonical = normalize_symbol(symbol)
        return canonical, derivatives_instrument_id(self.venue, canonical)

    def get_snapshot(self, symbol: str) -> DerivativesSnapshot:
        raise NotImplementedError


class BinanceDerivativesSource(PublicDerivativesSource):
    venue = Venue.BINANCE
    mark_endpoint = "https://fapi.binance.com/fapi/v1/premiumIndex"
    open_interest_endpoint = "https://fapi.binance.com/fapi/v1/openInterest"

    def get_snapshot(self, symbol: str) -> DerivativesSnapshot:
        canonical, instrument = self._canonical(symbol)
        premium, premium_received = self._get(self.mark_endpoint, {"symbol": instrument})
        if premium.get("symbol") != instrument:
            raise MarketDataError("Binance derivatives symbol mismatch")

        interest, interest_received = self._get(
            self.open_interest_endpoint, {"symbol": instrument},
        )
        if interest.get("symbol") != instrument:
            raise MarketDataError("Binance derivatives symbol mismatch")

        timestamp_ms = min(
            _timestamp(premium.get("time")),
            _timestamp(interest.get("time")),
        )
        received_at_ms = max(premium_received, interest_received)
        return normalize_derivatives_snapshot(
            venue=self.venue,
            symbol=canonical,
            instrument_id=instrument,
            timestamp_ms=timestamp_ms,
            received_at_ms=received_at_ms,
            mark_price=_decimal_value(premium.get("markPrice")),
            index_price=_decimal_value(premium.get("indexPrice")),
            funding_rate=_decimal_value(premium.get("lastFundingRate")),
            next_funding_time_ms=_optional_timestamp(premium.get("nextFundingTime")),
            open_interest=_decimal_value(interest.get("openInterest")),
            open_interest_unit=canonical.split("/", 1)[0],
            source_fields=("mark_price", "index_price", "funding_rate", "open_interest"),
            max_age_ms=self.settings.max_market_data_age_ms,
        )


class BybitDerivativesSource(PublicDerivativesSource):
    venue = Venue.BYBIT
    ticker_endpoint = "https://api.bybit.com/v5/market/tickers"
    open_interest_endpoint = "https://api.bybit.com/v5/market/open-interest"

    def _bybit_result(self, payload: dict[str, object]) -> dict[str, object]:
        if payload.get("retCode") != 0:
            raise MarketDataError("Bybit public derivatives API returned an error")
        return _object(payload.get("result"))

    def get_snapshot(self, symbol: str) -> DerivativesSnapshot:
        canonical, instrument = self._canonical(symbol)
        ticker_payload, ticker_received = self._get(
            self.ticker_endpoint, {"category": "linear", "symbol": instrument},
        )
        ticker_result = self._bybit_result(ticker_payload)
        if ticker_result.get("category") != "linear":
            raise MarketDataError("Bybit derivatives category mismatch")
        ticker = _single_object(
            ticker_result.get("list"), message="Bybit derivatives ticker unavailable",
        )
        if ticker.get("symbol") != instrument:
            raise MarketDataError("Bybit derivatives symbol mismatch")

        oi_payload, oi_received = self._get(
            self.open_interest_endpoint,
            {
                "category": "linear",
                "symbol": instrument,
                "intervalTime": "5min",
                "limit": "1",
            },
        )
        oi_result = self._bybit_result(oi_payload)
        if oi_result.get("category") != "linear" or oi_result.get("symbol") != instrument:
            raise MarketDataError("Bybit derivatives symbol mismatch")
        oi = _single_object(
            oi_result.get("list"), message="Bybit derivatives open interest unavailable",
        )

        timestamp_ms = min(
            _timestamp(ticker_payload.get("time")),
            _timestamp(oi.get("timestamp")),
        )
        received_at_ms = max(ticker_received, oi_received)
        return normalize_derivatives_snapshot(
            venue=self.venue,
            symbol=canonical,
            instrument_id=instrument,
            timestamp_ms=timestamp_ms,
            received_at_ms=received_at_ms,
            mark_price=_decimal_value(ticker.get("markPrice")),
            index_price=_decimal_value(ticker.get("indexPrice")),
            funding_rate=_decimal_value(ticker.get("fundingRate")),
            next_funding_time_ms=_optional_timestamp(ticker.get("nextFundingTime")),
            open_interest=_decimal_value(oi.get("openInterest")),
            open_interest_unit=canonical.split("/", 1)[0],
            source_fields=("mark_price", "index_price", "funding_rate", "open_interest"),
            max_age_ms=self.settings.max_market_data_age_ms,
        )


class OKXDerivativesSource(PublicDerivativesSource):
    venue = Venue.OKX
    funding_endpoint = "https://www.okx.com/api/v5/public/funding-rate"
    mark_endpoint = "https://www.okx.com/api/v5/public/mark-price"
    open_interest_endpoint = "https://www.okx.com/api/v5/public/open-interest"
    index_endpoint = "https://www.okx.com/api/v5/market/index-tickers"

    def _okx_row(
        self,
        payload: dict[str, object],
        *,
        message: str,
    ) -> dict[str, object]:
        if payload.get("code") != "0":
            raise MarketDataError("OKX public derivatives API returned an error")
        return _single_object(payload.get("data"), message=message)

    def get_snapshot(self, symbol: str) -> DerivativesSnapshot:
        canonical, instrument = self._canonical(symbol)
        index_instrument = canonical.replace("/", "-")

        funding_payload, funding_received = self._get(
            self.funding_endpoint, {"instId": instrument},
        )
        funding = self._okx_row(
            funding_payload, message="OKX derivatives funding unavailable",
        )
        if funding.get("instId") != instrument or funding.get("instType") != "SWAP":
            raise MarketDataError("OKX derivatives symbol mismatch")

        mark_payload, mark_received = self._get(
            self.mark_endpoint, {"instType": "SWAP", "instId": instrument},
        )
        mark = self._okx_row(mark_payload, message="OKX derivatives mark price unavailable")
        if mark.get("instId") != instrument or mark.get("instType") != "SWAP":
            raise MarketDataError("OKX derivatives symbol mismatch")

        oi_payload, oi_received = self._get(
            self.open_interest_endpoint, {"instType": "SWAP", "instId": instrument},
        )
        oi = self._okx_row(
            oi_payload, message="OKX derivatives open interest unavailable",
        )
        if oi.get("instId") != instrument or oi.get("instType") != "SWAP":
            raise MarketDataError("OKX derivatives symbol mismatch")

        index_payload, index_received = self._get(
            self.index_endpoint, {"instId": index_instrument},
        )
        index = self._okx_row(index_payload, message="OKX derivatives index unavailable")
        if index.get("instId") != index_instrument:
            raise MarketDataError("OKX derivatives symbol mismatch")

        timestamp_ms = min(
            _timestamp(funding.get("ts")),
            _timestamp(mark.get("ts")),
            _timestamp(oi.get("ts")),
            _timestamp(index.get("ts")),
        )
        received_at_ms = max(
            funding_received,
            mark_received,
            oi_received,
            index_received,
        )
        return normalize_derivatives_snapshot(
            venue=self.venue,
            symbol=canonical,
            instrument_id=instrument,
            timestamp_ms=timestamp_ms,
            received_at_ms=received_at_ms,
            mark_price=_decimal_value(mark.get("markPx")),
            index_price=_decimal_value(index.get("idxPx")),
            funding_rate=_decimal_value(funding.get("fundingRate")),
            next_funding_time_ms=_optional_timestamp(funding.get("nextFundingTime")),
            open_interest=_decimal_value(oi.get("oi")),
            open_interest_unit="contracts",
            source_fields=("mark_price", "index_price", "funding_rate", "open_interest"),
            max_age_ms=self.settings.max_market_data_age_ms,
        )
