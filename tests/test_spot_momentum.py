from decimal import Decimal

import httpx
import pytest

from quant_trading_platform.backtesting.spot import replay_trend
from quant_trading_platform.market_data.okx_candles import OKXCandleSource
from quant_trading_platform.market_data.spot_signal_service import SpotSignalService
from quant_trading_platform.strategies.spot_momentum import (
    DAY_MS,
    DailyCandle,
    relative_strength_signals,
    trend_signal,
    validate_candles,
)


def candles(symbol: str = "BTC/USDT", count: int = 110) -> tuple[DailyCandle, ...]:
    initial = Decimal("100") if symbol == "BTC/USDT" else Decimal("200")
    return tuple(DailyCandle(
        index * DAY_MS, initial + index, initial + index + Decimal("0.5"),
        initial + index - 1, initial + index, Decimal("10"),
    ) for index in range(count))


def test_completed_trend_breakout_and_exit_review() -> None:
    rows = candles()
    validate_candles(rows, now_ms=rows[-1].timestamp_ms + DAY_MS)
    assert trend_signal("BTC/USDT", rows).action == "entry_review"
    last = rows[-1]
    down = (*rows[:-1], DailyCandle(last.timestamp_ms, Decimal("90"), Decimal("91"),
                                    Decimal("89"), Decimal("90"), Decimal("10")))
    assert trend_signal("BTC/USDT", down).action == "exit_review"


def test_relative_strength_requires_all_pairs_on_same_date() -> None:
    series = {symbol: candles(symbol) for symbol in ("BTC/USDT", "ETH/USDT", "LTC/USDT")}
    signals = relative_strength_signals(series)
    assert sum(item.action == "entry_review" for item in signals) == 1
    with pytest.raises(ValueError, match="share"):
        relative_strength_signals({**series, "LTC/USDT": series["LTC/USDT"][:-1]})


def test_stale_gap_and_bad_ohlc_fail_closed() -> None:
    rows = candles()
    with pytest.raises(ValueError, match="missing"):
        validate_candles(rows, now_ms=rows[-1].timestamp_ms + 2 * DAY_MS)
    with pytest.raises(ValueError, match="discontinuous"):
        validate_candles((*rows[:50], *rows[51:]), now_ms=rows[-1].timestamp_ms + DAY_MS)
    bad = DailyCandle(rows[-1].timestamp_ms, Decimal("1"), Decimal("0.5"),
                      Decimal("1"), Decimal("1"), Decimal("1"))
    with pytest.raises(ValueError, match="Invalid"):
        validate_candles((*rows[:-1], bad), now_ms=rows[-1].timestamp_ms + DAY_MS)


def test_okx_source_excludes_incomplete_bar_and_has_no_credentials() -> None:
    rows = candles()
    payload = [[str(c.timestamp_ms), str(c.open), str(c.high), str(c.low), str(c.close),
                str(c.volume), "0", "0", "1"] for c in rows]
    payload.append([str(rows[-1].timestamp_ms + DAY_MS), "999", "999", "999", "999",
                    "1", "1", "1", "0"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params["bar"] == "1Dutc"
        assert request.url.params["instId"] == "BTC-USDT"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"code": "0", "data": payload[::-1]})

    source = OKXCandleSource(httpx.Client(transport=httpx.MockTransport(handler)))
    assert source.fetch("BTC/USDT", now_ms=rows[-1].timestamp_ms + DAY_MS) == rows
    payload[-1][-1] = "1"
    with pytest.raises(ValueError, match="Invalid|Incomplete|missing"):
        source.fetch("BTC/USDT", now_ms=rows[-1].timestamp_ms + DAY_MS)


@pytest.mark.asyncio
async def test_service_never_keeps_partial_data_after_failed_refresh() -> None:
    class Source:
        fail = False

        def fetch(self, symbol: str, *, now_ms: int) -> tuple[DailyCandle, ...]:
            if self.fail and symbol == "LTC/USDT":
                raise ValueError("unavailable")
            start = now_ms // DAY_MS - 110
            base = candles(symbol)
            return tuple(DailyCandle((start + i) * DAY_MS, row.open, row.high,
                                     row.low, row.close, row.volume)
                         for i, row in enumerate(base))

        def close(self) -> None:
            pass

    source = Source()
    service = SpotSignalService(source)  # type: ignore[arg-type]
    await service.poll_once()
    assert service.snapshot()["status"] == "ok"
    # Research universe is 4 pairs (SOL added); each pair gets a trend signal plus a ranking entry.
    assert len(service.snapshot()["signals"]) == 8  # type: ignore[arg-type]
    assert service.snapshot()["missing_symbols"] == []
    source.fail = True
    await service.poll_once()
    assert service.snapshot()["status"] == "unavailable"
    assert service.snapshot()["signals"] == []


def test_backtest_fills_next_open_and_charges_both_sides() -> None:
    rows = candles(count=105)
    result = replay_trend("BTC/USDT", rows)
    assert result.trades == 1
    assert result.fees_usdt > 0
    assert result.final_base_quantity > 0
    assert result.ending_equity_usdt < Decimal("150000")
    with pytest.raises(ValueError, match="costs"):
        replay_trend("BTC/USDT", rows, fee_pct=Decimal("NaN"))
