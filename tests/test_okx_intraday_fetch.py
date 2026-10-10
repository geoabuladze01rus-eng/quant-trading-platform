from decimal import Decimal

import httpx
import pytest

from quant_trading_platform.market_data.okx_candles import OKXCandleSource
from quant_trading_platform.strategies.spot_momentum import DailyCandle

FOUR_HOURS = 4 * 3_600_000


def bars(end_ts: int, count: int = 130) -> list[DailyCandle]:
    return [
        DailyCandle(
            end_ts - (count - 1 - index) * FOUR_HOURS,
            Decimal(100 + index), Decimal(101 + index), Decimal(99 + index),
            Decimal(100 + index), Decimal(1),
        )
        for index in range(count)
    ]


def okx_rows(candles: list[DailyCandle]) -> list[list[str]]:
    return [
        [str(c.timestamp_ms), str(c.open), str(c.high), str(c.low), str(c.close),
         str(c.volume), "0", "0", "1"]
        for c in candles
    ][::-1]


def test_fetch_requests_four_hour_bar_and_validates_interval() -> None:
    now_ms = 1_000 * FOUR_HOURS + 30 * 60_000
    end = (now_ms // FOUR_HOURS - 1) * FOUR_HOURS
    candles = bars(end)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["bar"] == "4H"
        assert request.url.params["instId"] == "BTC-USDT"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"code": "0", "data": okx_rows(candles)})

    source = OKXCandleSource(httpx.Client(transport=httpx.MockTransport(handler)))
    assert source.fetch("BTC/USDT", now_ms=now_ms, bar="4H") == tuple(candles)


def test_unconfirmed_open_bar_is_excluded() -> None:
    now_ms = 1_000 * FOUR_HOURS + 30 * 60_000
    end = (now_ms // FOUR_HOURS) * FOUR_HOURS  # current, still-open bar
    candles = bars(end)
    rows = okx_rows(candles)
    rows[0][8] = "0"  # newest row is the open bar, marked unconfirmed by OKX

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "data": rows})

    source = OKXCandleSource(httpx.Client(transport=httpx.MockTransport(handler)))
    assert source.fetch("BTC/USDT", now_ms=now_ms, bar="4H") == tuple(candles[:-1])


def test_confirmed_open_bar_fails_closed() -> None:
    now_ms = 1_000 * FOUR_HOURS + 30 * 60_000
    end = (now_ms // FOUR_HOURS) * FOUR_HOURS
    rows = okx_rows(bars(end))
    rows[0][8] = "1"  # claims the still-open bar is confirmed: must not be trusted

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "data": rows})

    source = OKXCandleSource(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match="Incomplete"):
        source.fetch("BTC/USDT", now_ms=now_ms, bar="4H")


def test_unknown_bar_is_rejected() -> None:
    source = OKXCandleSource(httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": "0", "data": []}))))
    with pytest.raises(ValueError, match="interval"):
        source.fetch("BTC/USDT", now_ms=1_000 * FOUR_HOURS, bar="3m")
