from copy import deepcopy
from decimal import Decimal

import httpx
import pytest

from quant_trading_platform.config import Settings
from quant_trading_platform.connectors.crypto.client import (
    BinanceConnector,
    BybitConnector,
    MarketDataError,
    OKXConnector,
    PublicCryptoConnector,
)
from quant_trading_platform.models import MarketType, Venue

CASES = [
    (BinanceConnector, Venue.BINANCE, "api.binance.com", "/api/v3/depth",
     {"lastUpdateId": 17, "bids": [["99", "2"], ["100", "3"]],
      "asks": [["102", "4"], ["101", "5"]]}),
    (BybitConnector, Venue.BYBIT, "api.bybit.com", "/v5/market/orderbook",
     {"retCode": 0, "result": {"s": "BTCUSDT", "cts": 9950, "ts": 9980,
      "b": [["99", "2"], ["100", "3"]], "a": [["102", "4"], ["101", "5"]]}}),
    (OKXConnector, Venue.OKX, "www.okx.com", "/api/v5/market/books",
     {"code": "0", "data": [{"ts": "9950", "bids": [["99", "2", "0", "1"],
      ["100", "3", "0", "2"]], "asks": [["102", "4", "0", "1"],
      ["101", "5", "0", "1"]]}]}),
]


@pytest.mark.parametrize("adapter,venue,host,path,payload", CASES)
def test_public_adapter_maps_normalized_order_book_to_quote(
    adapter: type[PublicCryptoConnector], venue: Venue, host: str, path: str,
    payload: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("quant_trading_platform.connectors.crypto.client._now_ms", lambda: 10000)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == host and request.url.path == path
        assert not any(name in request.headers for name in (
            "authorization", "cookie", "x-mbx-apikey", "x-bapi-api-key", "ok-access-key",
        ))
        assert "sign" not in str(request.url) and "secret" not in str(request.url)
        return httpx.Response(200, json=payload)

    with httpx.Client(
        transport=httpx.MockTransport(handle), auth=("private", "secret"),
        headers={"X-MBX-APIKEY": "secret"}, cookies={"session": "secret"},
    ) as http:
        connector = adapter(Settings(_env_file=None, binance_api_key="secret"), http)
        book = connector.get_order_book(" btc-usdt ")
        quote = book.to_quote()
        assert quote.symbol == "BTC/USDT" and quote.venue == venue
        assert quote.market_type == MarketType.CRYPTO
        assert (quote.bid, quote.ask) == (Decimal("100"), Decimal("101"))
        assert (quote.bid_size, quote.ask_size) == (Decimal("3"), Decimal("5"))
        assert len(book.bids) == 2 and len(book.asks) == 2
        assert quote.timestamp_ms == (10000 if venue == Venue.BINANCE else 9950)
        assert book.timestamp_source == ("request_start" if venue == Venue.BINANCE else "exchange")
        assert connector.get_ticker("BTCUSDT") == quote
        if venue == Venue.OKX:
            assert requests[0].url.params["instId"] == "BTC-USDT"
        else:
            assert requests[0].url.params["symbol"] == "BTCUSDT"
        if venue == Venue.BYBIT:
            assert requests[0].url.params["category"] == "spot"
        with pytest.raises(MarketDataError, match="Balances"):
            connector.get_balances()
        connector.close()
        assert not http.is_closed  # An injected client's owner controls its lifetime.


@pytest.mark.parametrize("adapter,venue,host,path,payload", CASES)
@pytest.mark.parametrize("status", [403, 429, 500, 302])
def test_public_http_errors_and_redirects_fail_closed(
    adapter: type[PublicCryptoConnector], venue: Venue, host: str, path: str,
    payload: dict[str, object], status: int,
) -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, text="secret", headers={"Location": "https://private.invalid"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(MarketDataError) as error:
            adapter(Settings(_env_file=None), http).get_order_book("BTC/USDT")
        assert "secret" not in str(error.value)
        assert len(calls) == 1


@pytest.mark.parametrize("timestamp", [8000, 11000])
@pytest.mark.parametrize("adapter", [BybitConnector, OKXConnector])
def test_exchange_stale_and_future_snapshots_rejected(
    adapter: type[PublicCryptoConnector], timestamp: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("quant_trading_platform.connectors.crypto.client._now_ms", lambda: 10000)
    if adapter is BybitConnector:
        payload = {"retCode": 0, "result": {"s": "BTCUSDT", "ts": timestamp,
                   "b": [["99", "1"]], "a": [["100", "1"]]}}
    else:
        payload = {"code": "0", "data": [{"ts": str(timestamp),
                   "bids": [["99", "1"]], "asks": [["100", "1"]]}]}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    with (
        httpx.Client(transport=transport) as http,
        pytest.raises(ValueError, match="Stale|Future"),
    ):
        adapter(Settings(_env_file=None), http).get_order_book("BTCUSDT")


def test_binance_slow_request_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([8000, 10000])
    monkeypatch.setattr(
        "quant_trading_platform.connectors.crypto.client._now_ms", lambda: next(clock),
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={"bids": [["99", "1"]], "asks": [["100", "1"]]},
    ))) as http, pytest.raises(ValueError, match="Stale"):
        BinanceConnector(Settings(_env_file=None), http).get_order_book("BTCUSDT")


@pytest.mark.parametrize("adapter", [BinanceConnector, BybitConnector, OKXConnector])
def test_timeout_and_malformed_response_sanitized(adapter: type[PublicCryptoConnector]) -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret")

    with (
        httpx.Client(transport=httpx.MockTransport(timeout)) as http,
        pytest.raises(MarketDataError, match="request failed"),
    ):
        adapter(Settings(_env_file=None), http).get_order_book("BTCUSDT")
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text="invalid"))
    with (
        httpx.Client(transport=transport) as http,
        pytest.raises(MarketDataError, match="request failed"),
    ):
        adapter(Settings(_env_file=None), http).get_order_book("BTCUSDT")


INSTRUMENT_CASES = [
    (
        BinanceConnector,
        "/api/v3/exchangeInfo",
        {
            "symbols": [
                {
                    "symbol": "LTCUSDT",
                    "baseAsset": "LTC",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                        {"filterType": "NOTIONAL", "minNotional": "5"},
                    ],
                }
            ]
        },
        ("0.01", "0.001", "0.001", "5"),
    ),
    (
        BybitConnector,
        "/v5/market/instruments-info",
        {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "LTCUSDT",
                        "baseCoin": "LTC",
                        "quoteCoin": "USDT",
                        "status": "Trading",
                        "priceFilter": {"tickSize": "0.01"},
                        "lotSizeFilter": {
                            "basePrecision": "0.00001",
                            "minOrderQty": "0.00001",
                            "minOrderAmt": "5",
                        },
                    }
                ]
            },
        },
        ("0.01", "0.00001", "0.00001", "5"),
    ),
    (
        OKXConnector,
        "/api/v5/public/instruments",
        {
            "code": "0",
            "data": [
                {
                    "instId": "LTC-USDT",
                    "baseCcy": "LTC",
                    "quoteCcy": "USDT",
                    "instType": "SPOT",
                    "state": "live",
                    "tickSz": "0.01",
                    "lotSz": "0.000001",
                    "minSz": "0.01",
                }
            ],
        },
        ("0.01", "0.000001", "0.01", None),
    ),
]


@pytest.mark.parametrize("adapter,path,payload,expected", INSTRUMENT_CASES)
def test_public_instrument_rules_are_normalized_without_quote_conversion(
    adapter: type[PublicCryptoConnector],
    path: str,
    payload: dict[str, object],
    expected: tuple[str, str, str, str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("quant_trading_platform.connectors.crypto.client._now_ms", lambda: 10000)

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == path
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
        if adapter is BinanceConnector:
            assert dict(request.url.params) == {"symbol": "LTCUSDT"}
        elif adapter is BybitConnector:
            assert dict(request.url.params) == {"category": "spot", "symbol": "LTCUSDT"}
        else:
            assert dict(request.url.params) == {"instType": "SPOT", "instId": "LTC-USDT"}
        return httpx.Response(200, json=payload)

    with httpx.Client(
        transport=httpx.MockTransport(handle),
        auth=("private", "secret"),
        headers={
            "X-MBX-APIKEY": "secret",
            "X-BAPI-API-KEY": "secret",
            "OK-ACCESS-KEY": "secret",
        },
        cookies={"session": "secret"},
    ) as http:
        rules = adapter(Settings(_env_file=None), http).get_instrument_rules("ltc-usdt")
    assert rules.symbol == "LTC/USDT"
    assert rules.venue == adapter.venue
    assert rules.base_asset == "LTC" and rules.quote_asset == "USDT"
    assert tuple(
        None if value is None else str(value)
        for value in (
            rules.tick_size,
            rules.quantity_step,
            rules.min_quantity,
            rules.min_notional,
        )
    ) == expected
    assert rules.status == "trading" and rules.timestamp_ms == 10000
    assert rules.source in {
        "binance:exchangeInfo",
        "bybit:instruments-info",
        "okx:public-instruments",
    }


@pytest.mark.parametrize("adapter", [BinanceConnector, BybitConnector, OKXConnector])
def test_instrument_discovery_rejects_unsupported_quote_before_network(
    adapter: type[PublicCryptoConnector],
) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: pytest.fail("network must not be called"))
        ) as http,
        pytest.raises(ValueError, match="Unsupported"),
    ):
        adapter(Settings(_env_file=None), http).get_instrument_rules("LTC/USDC")


@pytest.mark.parametrize("adapter,path,payload,expected", INSTRUMENT_CASES)
def test_instrument_discovery_rejects_unavailable_pair(
    adapter: type[PublicCryptoConnector],
    path: str,
    payload: dict[str, object],
    expected: tuple[str, str, str, str | None],
) -> None:
    del path, expected
    unavailable = deepcopy(payload)
    if adapter is BinanceConnector:
        unavailable["symbols"][0]["status"] = "BREAK"  # type: ignore[index]
    elif adapter is BybitConnector:
        unavailable["result"]["list"][0]["status"] = "PendingOpen"  # type: ignore[index]
    else:
        unavailable["data"][0]["state"] = "suspend"  # type: ignore[index]
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=unavailable))
    with (
        httpx.Client(transport=transport) as http,
        pytest.raises(MarketDataError, match="unavailable"),
    ):
        adapter(Settings(_env_file=None), http).get_instrument_rules("LTC/USDT")


@pytest.mark.parametrize("adapter,path,payload,expected", INSTRUMENT_CASES)
def test_instrument_discovery_rejects_nonpositive_rule(
    adapter: type[PublicCryptoConnector],
    path: str,
    payload: dict[str, object],
    expected: tuple[str, str, str, str | None],
) -> None:
    del path, expected
    invalid = deepcopy(payload)
    if adapter is BinanceConnector:
        invalid["symbols"][0]["filters"][0]["tickSize"] = "0"  # type: ignore[index]
    elif adapter is BybitConnector:
        invalid["result"]["list"][0]["priceFilter"]["tickSize"] = "NaN"  # type: ignore[index]
    else:
        invalid["data"][0]["tickSz"] = "-0.01"  # type: ignore[index]
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=invalid))
    with (
        httpx.Client(transport=transport) as http,
        pytest.raises(MarketDataError, match="tick size"),
    ):
        adapter(Settings(_env_file=None), http).get_instrument_rules("LTC/USDT")
