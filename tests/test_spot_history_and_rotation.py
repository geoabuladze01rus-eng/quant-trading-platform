from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from quant_trading_platform.backtesting.spot import replay_trend
from quant_trading_platform.backtesting.spot_rotation import replay_rotation
from quant_trading_platform.market_data.okx_history import OKXHistorySource
from quant_trading_platform.strategies.spot_momentum import DAY_MS, SYMBOLS, DailyCandle


def history(count: int = 215) -> dict[str, tuple[DailyCandle, ...]]:
    def series(symbol: str) -> tuple[DailyCandle, ...]:
        step = {"BTC/USDT": 1, "ETH/USDT": 2, "LTC/USDT": 3}[symbol]
        return tuple(DailyCandle(
            day * DAY_MS, Decimal(100 + day * step), Decimal("100.5") + day * step,
            Decimal(99 + day * step), Decimal(100 + day * step), Decimal("10"),
        ) for day in range(count))
    return {symbol: series(symbol) for symbol in SYMBOLS}


def test_history_paginates_to_exact_completed_utc_window() -> None:
    candles = history()["BTC/USDT"]
    requests: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["bar"] == "1Dutc"
        assert request.url.params["instId"] == "BTC-USDT"
        assert "authorization" not in request.headers
        cursor = request.url.params.get("after")
        requests.append(cursor)
        subset = [c for c in candles if cursor is None or c.timestamp_ms < int(cursor)]
        rows = [[str(c.timestamp_ms), str(c.open), str(c.high), str(c.low), str(c.close),
                 str(c.volume), "0", "0", "1"] for c in subset[-100:][::-1]]
        return httpx.Response(200, json={"code": "0", "data": rows})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = OKXHistorySource(client)
    got = source.fetch("BTC/USDT", days=215, now_ms=215 * DAY_MS)
    assert got == candles
    assert len(requests) == 3


def test_history_rejects_missing_bar() -> None:
    candles = history()["BTC/USDT"]

    def handler(request: httpx.Request) -> httpx.Response:
        subset = [c for c in candles if c.timestamp_ms != 130 * DAY_MS]
        cursor = request.url.params.get("after")
        subset = [c for c in subset if cursor is None or c.timestamp_ms < int(cursor)]
        rows = [[str(c.timestamp_ms), str(c.open), str(c.high), str(c.low), str(c.close),
                 str(c.volume), "0", "0", "1"] for c in subset[-100:][::-1]]
        return httpx.Response(200, json={"code": "0", "data": rows})

    source = OKXHistorySource(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match="incomplete"):
        source.fetch("BTC/USDT", days=215, now_ms=215 * DAY_MS)


def test_history_rejects_stalled_pagination() -> None:
    candles = history()["BTC/USDT"][-100:]
    rows = [[str(c.timestamp_ms), str(c.open), str(c.high), str(c.low), str(c.close),
             str(c.volume), "0", "0", "1"] for c in candles[::-1]]
    source = OKXHistorySource(httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": "0", "data": rows})
    )))
    with pytest.raises(ValueError, match="pagination"):
        source.fetch("BTC/USDT", days=215, now_ms=215 * DAY_MS)


def test_rotation_is_long_only_capped_and_never_looks_at_future_close() -> None:
    series = history()
    result = replay_rotation(series, fee_pct=Decimal("0.35"))
    assert result.trades
    assert result.trades[0].side == "buy"
    assert result.trades[0].symbol == "LTC/USDT"
    assert result.trades[0].quantity * result.trades[0].effective_price < Decimal("50000")
    assert result.fees_usdt > 0
    assert result.max_drawdown_pct >= 0
    benchmark = replay_rotation(series, strategy="btc_hold")
    assert benchmark.trades[0].symbol == "BTC/USDT"
    changed = {**series, "ETH/USDT": (*series["ETH/USDT"][:-1], replace(
        series["ETH/USDT"][-1], close=Decimal("9999"), high=Decimal("10000")
    ))}
    assert replay_rotation(changed).trades == result.trades


def test_holdout_and_costs_are_explicit() -> None:
    series = history()
    full = replay_rotation(series)
    holdout = replay_rotation(series, first_signal_index=180)
    assert holdout.trades[0].timestamp_ms == 181 * DAY_MS
    assert len(holdout.trades) <= len(full.trades)
    expensive = replay_rotation(series, fee_pct=Decimal("1"))
    assert expensive.ending_equity_usdt < full.ending_equity_usdt
    assert replay_trend("BTC/USDT", series["BTC/USDT"], first_signal_index=180).fees_usdt > 0
    with pytest.raises(ValueError, match="costs"):
        replay_rotation(series, spread_pct=Decimal("NaN"))
