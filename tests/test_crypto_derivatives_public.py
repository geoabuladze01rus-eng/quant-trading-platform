"""Public request contracts, identity rejection and atomic snapshot failures."""

from decimal import Decimal

import httpx
import pytest

from quant_trading_platform.connectors.crypto.client import MarketDataError
from quant_trading_platform.connectors.crypto.derivatives import (
    BinanceDerivativesSource,
    BybitDerivativesSource,
    OKXDerivativesSource,
)

CASES = [
    (
        BinanceDerivativesSource,
        [
            (
                "/fapi/v1/premiumIndex",
                {"symbol": "BTCUSDT"},
                {
                    "symbol": "BTCUSDT",
                    "time": 1000,
                    "markPrice": "100",
                    "indexPrice": "99",
                    "lastFundingRate": "-0.001",
                    "nextFundingTime": 2000,
                },
            ),
            (
                "/fapi/v1/openInterest",
                {"symbol": "BTCUSDT"},
                {"symbol": "BTCUSDT", "time": 1001, "openInterest": "2"},
            ),
        ],
        "base_asset",
    ),
    (
        BybitDerivativesSource,
        [
            (
                "/v5/market/tickers",
                {"category": "linear", "symbol": "BTCUSDT"},
                {
                    "retCode": 0,
                    "time": 1000,
                    "result": {
                        "category": "linear",
                        "list": [
                            {
                                "symbol": "BTCUSDT",
                                "markPrice": "100",
                                "indexPrice": "99",
                                "fundingRate": "-0.001",
                                "nextFundingTime": "2000",
                            }
                        ],
                    },
                },
            ),
            (
                "/v5/market/open-interest",
                {"category": "linear", "symbol": "BTCUSDT", "intervalTime": "5min", "limit": "1"},
                {
                    "retCode": 0,
                    "time": 1001,
                    "result": {
                        "category": "linear",
                        "symbol": "BTCUSDT",
                        "list": [{"openInterest": "2", "timestamp": "1000"}],
                    },
                },
            ),
        ],
        "base_asset",
    ),
    (
        OKXDerivativesSource,
        [
            (
                "/api/v5/public/funding-rate",
                {"instId": "BTC-USDT-SWAP"},
                {
                    "code": "0",
                    "data": [
                        {
                            "instId": "BTC-USDT-SWAP",
                            "ts": "1000",
                            "fundingRate": "-0.001",
                            "nextFundingTime": "2000",
                        }
                    ],
                },
            ),
            (
                "/api/v5/public/mark-price",
                {"instType": "SWAP", "instId": "BTC-USDT-SWAP"},
                {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "ts": "1000", "markPx": "100"}]},
            ),
            (
                "/api/v5/public/open-interest",
                {"instType": "SWAP", "instId": "BTC-USDT-SWAP"},
                {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "ts": "1001", "oi": "2"}]},
            ),
            (
                "/api/v5/market/index-tickers",
                {"instId": "BTC-USDT"},
                {"code": "0", "data": [{"instId": "BTC-USDT", "ts": "1000", "idxPx": "99"}]},
            ),
        ],
        "contracts",
    ),
]


@pytest.mark.parametrize("source_cls,rows,unit", CASES)
@pytest.mark.parametrize(
    "fault", [None, "second_failure", "identity", "nonfinite", "future_component"]
)
def test_public_snapshot_contract(source_cls, rows, unit, fault):
    calls = []

    def handler(request):
        index = len(calls)
        calls.append(request)
        path, params, payload = rows[index]
        assert request.method == "GET"
        assert request.url.path == path
        assert dict(request.url.params) == params
        assert request.extensions["timeout"]["read"] == 5
        for name in (
            "authorization",
            "cookie",
            "x-mbx-apikey",
            "x-bapi-api-key",
            "ok-access-key",
            "ok-access-sign",
        ):
            assert name not in request.headers
        if fault == "future_component" and index == 1:
            import copy

            payload = copy.deepcopy(payload)
            if source_cls in (BinanceDerivativesSource, BybitDerivativesSource):
                payload["time"] = 9999
            else:
                payload["data"][0]["ts"] = "9999"
        if fault == "second_failure" and index == 1:
            return httpx.Response(503, text="secret upstream body")
        import copy

        payload = copy.deepcopy(payload)
        if index == 0 and fault in ("identity", "nonfinite"):
            if source_cls == BinanceDerivativesSource:
                payload["symbol" if fault == "identity" else "markPrice"] = (
                    "ETHUSDT" if fault == "identity" else "NaN"
                )
            elif source_cls == BybitDerivativesSource:
                payload["result"]["list"][0]["symbol" if fault == "identity" else "markPrice"] = (
                    "ETHUSDT" if fault == "identity" else "NaN"
                )
            else:
                payload["data"][0]["instId" if fault == "identity" else "fundingRate"] = (
                    "ETH-USDT-SWAP" if fault == "identity" else "NaN"
                )
        return httpx.Response(200, json=payload)

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        auth=("user", "secret"),
        cookies={"session": "secret"},
        headers={"X-MBX-APIKEY": "secret", "OK-ACCESS-KEY": "secret", "X-BAPI-API-KEY": "secret"},
    ) as client:
        source = source_cls(client=client, clock=lambda: 1002)
        if fault:
            with pytest.raises(MarketDataError) as error:
                source.get_snapshot("BTC/USDT")
            assert "secret" not in str(error.value)
            assert error.value.__cause__ is None
        else:
            result = source.get_snapshot("BTC/USDT")
            assert len(calls) == len(rows)
            assert result.mark_price == Decimal("100")
            assert result.index_price == Decimal("99")
            assert result.funding_rate == Decimal("-0.001")
            assert result.open_interest == Decimal("2")
            assert result.open_interest_unit == unit
            assert result.timestamp_ms == 1000
            assert result.received_at_ms == 1002
        source.close()
        assert not client.is_closed
