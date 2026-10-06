"""Credential-free fixed-endpoint public perpetual snapshots; no execution methods."""

from collections.abc import Callable
from time import time_ns
from typing import Protocol

import httpx

from quant_trading_platform.connectors.crypto.client import MarketDataError, _object, _timestamp
from quant_trading_platform.market_data.derivatives import (
    DerivativesSnapshot,
    instrument_for,
    normalize_derivatives_snapshot,
)
from quant_trading_platform.models import Venue, normalize_symbol


class PublicDerivativesSource(Protocol):
    venue: Venue

    def get_snapshot(self, symbol: str) -> DerivativesSnapshot: ...
    def close(self) -> None: ...


class _PublicSource:
    venue: Venue
    host: str

    def __init__(
        self,
        client: httpx.Client | None = None,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self.clock = clock

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _get(self, path: str, params: dict[str, str]) -> dict[str, object]:
        if self._client is None:
            self._client = httpx.Client(trust_env=False)
        request = httpx.Request(
            "GET",
            self.host + path,
            params=params,
            extensions={"timeout": httpx.Timeout(5).as_dict()},
        )
        response = self._client.send(request, auth=None, follow_redirects=False)
        response.raise_for_status()
        return _object(response.json())

    def _oldest(self, *timestamps: int) -> int:
        if any(t <= 0 or t > self.clock() for t in timestamps):
            raise ValueError("Invalid component timestamp")
        return min(timestamps)

    def get_snapshot(self, symbol: str) -> DerivativesSnapshot:
        try:
            symbol = normalize_symbol(symbol)
            instrument = instrument_for(self.venue, symbol)
            return self._read(symbol, instrument)
        except Exception:
            raise MarketDataError("Public derivatives snapshot unavailable") from None

    def _read(self, symbol: str, instrument: str) -> DerivativesSnapshot:
        raise NotImplementedError


class BinanceDerivativesSource(_PublicSource):
    venue = Venue.BINANCE
    host = "https://fapi.binance.com"

    def _read(self, symbol: str, instrument: str) -> DerivativesSnapshot:
        mark = self._get("/fapi/v1/premiumIndex", {"symbol": instrument})
        oi = self._get("/fapi/v1/openInterest", {"symbol": instrument})
        if any(row.get("symbol") != instrument for row in (mark, oi)):
            raise ValueError("Instrument mismatch")
        return normalize_derivatives_snapshot(
            venue=self.venue,
            symbol=symbol,
            instrument_id=instrument,
            timestamp_ms=self._oldest(_timestamp(mark.get("time")), _timestamp(oi.get("time"))),
            received_at_ms=self.clock(),
            mark_price=mark.get("markPrice"),
            index_price=mark.get("indexPrice"),
            funding_rate=mark.get("lastFundingRate"),
            next_funding_time_ms=(
                None if mark.get("nextFundingTime") is None else _timestamp(mark["nextFundingTime"])
            ),
            open_interest=oi.get("openInterest"),
            open_interest_unit="base_asset",
            source_fields=("premiumIndex", "openInterest"),
        )


class BybitDerivativesSource(_PublicSource):
    venue = Venue.BYBIT
    host = "https://api.bybit.com"

    def _result(self, path: str, params: dict[str, str]) -> tuple[dict[str, object], int]:
        data = self._get(path, params)
        if data.get("retCode") != 0:
            raise ValueError("Public API error")
        result = _object(data.get("result"))
        if result.get("category", "linear") != "linear":
            raise ValueError("Contract category mismatch")
        return result, _timestamp(data.get("time"))

    def _read(self, symbol: str, instrument: str) -> DerivativesSnapshot:
        result, timestamp = self._result(
            "/v5/market/tickers", {"category": "linear", "symbol": instrument}
        )
        mark = _one(result.get("list"))
        result, oi_time = self._result(
            "/v5/market/open-interest",
            {
                "category": "linear",
                "symbol": instrument,
                "intervalTime": "5min",
                "limit": "1",
            },
        )
        oi = _one(result.get("list"))
        if mark.get("symbol") != instrument or result.get("symbol") != instrument:
            raise ValueError("Instrument mismatch")
        return normalize_derivatives_snapshot(
            venue=self.venue,
            symbol=symbol,
            instrument_id=instrument,
            timestamp_ms=self._oldest(timestamp, oi_time, _timestamp(oi.get("timestamp"))),
            received_at_ms=self.clock(),
            mark_price=mark.get("markPrice"),
            index_price=mark.get("indexPrice"),
            funding_rate=mark.get("fundingRate"),
            next_funding_time_ms=(
                None if mark.get("nextFundingTime") is None else _timestamp(mark["nextFundingTime"])
            ),
            open_interest=oi.get("openInterest"),
            open_interest_unit="base_asset",
            source_fields=("linear_ticker", "open_interest_5min"),
        )


def _one(value: object) -> dict[str, object]:
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError("Expected one public snapshot row")
    return _object(value[0])


class OKXDerivativesSource(_PublicSource):
    venue = Venue.OKX
    host = "https://www.okx.com"

    def _row(self, path: str, params: dict[str, str], instrument: str) -> dict[str, object]:
        data = self._get(path, params)
        if data.get("code") != "0":
            raise ValueError("Public API error")
        row = _one(data.get("data"))
        if row.get("instId") != instrument:
            raise ValueError("Instrument mismatch")
        return row

    def _read(self, symbol: str, instrument: str) -> DerivativesSnapshot:
        funding = self._row("/api/v5/public/funding-rate", {"instId": instrument}, instrument)
        params = {"instType": "SWAP", "instId": instrument}
        mark = self._row("/api/v5/public/mark-price", params, instrument)
        oi = self._row("/api/v5/public/open-interest", params, instrument)
        index_id = symbol.replace("/", "-")
        index = self._row("/api/v5/market/index-tickers", {"instId": index_id}, index_id)
        return normalize_derivatives_snapshot(
            venue=self.venue,
            symbol=symbol,
            instrument_id=instrument,
            timestamp_ms=self._oldest(
                *(_timestamp(row.get("ts")) for row in (funding, mark, oi, index))
            ),
            received_at_ms=self.clock(),
            mark_price=mark.get("markPx"),
            index_price=index.get("idxPx"),
            funding_rate=funding.get("fundingRate"),
            next_funding_time_ms=(
                None
                if funding.get("nextFundingTime") is None
                else _timestamp(funding["nextFundingTime"])
            ),
            open_interest=oi.get("oi"),
            open_interest_unit="contracts",
            source_fields=("funding_rate", "mark_price", "open_interest", "index_tickers"),
        )
