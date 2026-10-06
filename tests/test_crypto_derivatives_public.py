from decimal import Decimal

import httpx
import pytest

from quant_trading_platform.config import Settings
from quant_trading_platform.connectors.crypto.client import MarketDataError
from quant_trading_platform.connectors.crypto.derivatives import (
    BinanceDerivativesSource,
    BybitDerivativesSource,
    OKXDerivativesSource,
)
from quant_trading_platform.models import Venue


def _assert_public_request(request: httpx.Request) -> None:
    assert request.method == "GET"
    assert not any(
        name in request.headers
        for name in (
            "authorization",
            "cookie",
            "x-mbx-apikey",
            "x-bapi-api-key",
            "ok-access-key",
        )
    )
    lowered = str(request.url).lower()
    assert "signature=" not in lowered and "secret" not in lowered


def test_binance_derivatives_maps_mark_funding_and_open_interest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "quant_trading_platform.connectors.crypto.derivatives._now_ms", lambda: 10_000,
    )
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        _assert_public_request(request)
        if request.url.path == "/fapi/v1/premiumIndex":
            assert request.url.params["symbol"] == "BTCUSDT"
            return httpx.Response(200, json={
                "symbol": "BTCUSDT",
                "markPrice": "100.5",
                "indexPrice": "100.0",
                "lastFundingRate": "-0.0001",
                "nextFundingTime": 20_000,
                "time": 9_950,
            })
        assert request.url.path == "/fapi/v1/openInterest"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(200, json={
            "symbol": "BTCUSDT",
            "openInterest": "123.45",
            "time": 9_960,
        })

    with httpx.Client(
        transport=httpx.MockTransport(handle),
        auth=("private", "secret"),
        headers={"X-MBX-APIKEY": "secret"},
        cookies={"session": "secret"},
    ) as http:
        source = BinanceDerivativesSource(Settings(_env_file=None), http)
        snapshot = source.get_snapshot("BTC/USDT")
        assert snapshot.venue == Venue.BINANCE
        assert snapshot.mark_price == Decimal("100.5")
        assert snapshot.index_price == Decimal("100.0")
        assert snapshot.funding_rate == Decimal("-0.0001")
        assert snapshot.open_interest == Decimal("123.45")
        assert snapshot.open_interest_unit == "BTC"
        assert snapshot.timestamp_ms == 9_950
        source.close()
        assert not http.is_closed
    assert len(requests) == 2


def test_bybit_derivatives_maps_linear_ticker_and_open_interest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "quant_trading_platform.connectors.crypto.derivatives._now_ms", lambda: 10_000,
    )

    def handle(request: httpx.Request) -> httpx.Response:
        _assert_public_request(request)
        assert request.url.params["category"] == "linear"
        assert request.url.params["symbol"] == "ETHUSDT"
        if request.url.path == "/v5/market/tickers":
            return httpx.Response(200, json={
                "retCode": 0,
                "result": {
                    "category": "linear",
                    "list": [{
                        "symbol": "ETHUSDT",
                        "markPrice": "2000.5",
                        "indexPrice": "1999.5",
                        "fundingRate": "0.0002",
                        "nextFundingTime": "20000",
                    }],
                },
                "time": 9_970,
            })
        assert request.url.path == "/v5/market/open-interest"
        assert request.url.params["intervalTime"] == "5min"
        assert request.url.params["limit"] == "1"
        return httpx.Response(200, json={
            "retCode": 0,
            "result": {
                "category": "linear",
                "symbol": "ETHUSDT",
                "list": [{"openInterest": "234.5", "timestamp": "9950"}],
                "nextPageCursor": "",
            },
            "time": 9_980,
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        snapshot = BybitDerivativesSource(Settings(_env_file=None), http).get_snapshot("ETHUSDT")
    assert snapshot.venue == Venue.BYBIT
    assert snapshot.instrument_id == "ETHUSDT"
    assert snapshot.mark_price == Decimal("2000.5")
    assert snapshot.index_price == Decimal("1999.5")
    assert snapshot.funding_rate == Decimal("0.0002")
    assert snapshot.open_interest == Decimal("234.5")
    assert snapshot.open_interest_unit == "ETH"
    assert snapshot.timestamp_ms == 9_950


def test_okx_derivatives_maps_public_swap_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "quant_trading_platform.connectors.crypto.derivatives._now_ms", lambda: 10_000,
    )

    def handle(request: httpx.Request) -> httpx.Response:
        _assert_public_request(request)
        path = request.url.path
        if path == "/api/v5/public/funding-rate":
            assert request.url.params["instId"] == "SOL-USDT-SWAP"
            return httpx.Response(200, json={"code": "0", "data": [{
                "instType": "SWAP",
                "instId": "SOL-USDT-SWAP",
                "fundingRate": "0.0003",
                "nextFundingTime": "20000",
                "ts": "9970",
            }]})
        if path == "/api/v5/public/mark-price":
            assert request.url.params["instType"] == "SWAP"
            assert request.url.params["instId"] == "SOL-USDT-SWAP"
            return httpx.Response(200, json={"code": "0", "data": [{
                "instType": "SWAP",
                "instId": "SOL-USDT-SWAP",
                "markPx": "150.5",
                "ts": "9960",
            }]})
        if path == "/api/v5/public/open-interest":
            assert request.url.params["instType"] == "SWAP"
            assert request.url.params["instId"] == "SOL-USDT-SWAP"
            return httpx.Response(200, json={"code": "0", "data": [{
                "instType": "SWAP",
                "instId": "SOL-USDT-SWAP",
                "oi": "345.5",
                "oiCcy": "345.5",
                "oiUsd": "51825",
                "ts": "9950",
            }]})
        assert path == "/api/v5/market/index-tickers"
        assert request.url.params["instId"] == "SOL-USDT"
        return httpx.Response(200, json={"code": "0", "data": [{
            "instId": "SOL-USDT",
            "idxPx": "150.0",
            "ts": "9940",
        }]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        snapshot = OKXDerivativesSource(Settings(_env_file=None), http).get_snapshot("SOL-USDT")
    assert snapshot.venue == Venue.OKX
    assert snapshot.instrument_id == "SOL-USDT-SWAP"
    assert snapshot.mark_price == Decimal("150.5")
    assert snapshot.index_price == Decimal("150.0")
    assert snapshot.funding_rate == Decimal("0.0003")
    assert snapshot.open_interest == Decimal("345.5")
    assert snapshot.open_interest_unit == "contracts"
    assert snapshot.timestamp_ms == 9_940


@pytest.mark.parametrize(
    "source_cls",
    [BinanceDerivativesSource, BybitDerivativesSource, OKXDerivativesSource],
)
def test_derivatives_source_sanitizes_http_failures(
    source_cls: type[BinanceDerivativesSource]
    | type[BybitDerivativesSource]
    | type[OKXDerivativesSource],
) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(500, text="secret")),
        ) as http,
        pytest.raises(MarketDataError) as error,
    ):
        source_cls(Settings(_env_file=None), http).get_snapshot("BTC/USDT")
    assert "secret" not in str(error.value)


def test_second_request_failure_never_returns_partial_binance_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "quant_trading_platform.connectors.crypto.derivatives._now_ms", lambda: 10_000,
    )
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={
                "symbol": "BTCUSDT",
                "markPrice": "100",
                "indexPrice": "100",
                "lastFundingRate": "0",
                "nextFundingTime": 20_000,
                "time": 9_950,
            })
        return httpx.Response(503, text="partial secret")

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(MarketDataError, match="request failed") as error,
    ):
        BinanceDerivativesSource(Settings(_env_file=None), http).get_snapshot("BTCUSDT")
    assert "secret" not in str(error.value)


def test_rejects_exchange_response_for_wrong_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "quant_trading_platform.connectors.crypto.derivatives._now_ms", lambda: 10_000,
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("premiumIndex"):
            return httpx.Response(200, json={
                "symbol": "ETHUSDT",
                "markPrice": "100",
                "indexPrice": "100",
                "lastFundingRate": "0",
                "nextFundingTime": 20_000,
                "time": 9_950,
            })
        return httpx.Response(200, json={
            "symbol": "BTCUSDT",
            "openInterest": "1",
            "time": 9_950,
        })

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(MarketDataError, match="symbol mismatch"),
    ):
        BinanceDerivativesSource(Settings(_env_file=None), http).get_snapshot("BTC/USDT")
