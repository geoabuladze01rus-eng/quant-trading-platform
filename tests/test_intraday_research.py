import asyncio
from decimal import Decimal
from time import time

from quant_trading_platform.market_data.intraday_research import IntradayResearchService
from quant_trading_platform.strategies.spot_momentum import RESEARCH_SYMBOLS, DailyCandle

H1 = 3_600_000
H4 = 4 * H1


def last_closed(now_ms: int, interval_ms: int) -> int:
    return (now_ms // interval_ms - 1) * interval_ms


def series(
    closes: list[Decimal], interval_ms: int, end: int, volume_last: Decimal,
) -> tuple[DailyCandle, ...]:
    rows = []
    for index, close in enumerate(closes):
        volume = volume_last if index == len(closes) - 1 else Decimal(1)
        rows.append(DailyCandle(
            end - (len(closes) - 1 - index) * interval_ms,
            close, close + Decimal("0.5"), close - Decimal("0.5"), close, volume,
        ))
    return tuple(rows)


class FakeSource:
    def __init__(self, failing: tuple[str, ...] = ()) -> None:
        self.failing = set(failing)
        self.closed = False

    def fetch(self, symbol: str, *, now_ms: int, bar: str) -> tuple[DailyCandle, ...]:
        if symbol in self.failing:
            raise ValueError("OKX public candles unavailable")
        if bar == "4Hutc":
            end = last_closed(now_ms, H4)
            closes = [Decimal(100) + Decimal("0.5") * i for i in range(120)]
            return series(closes, H4, end, Decimal(1))
        end = last_closed(now_ms, H1)
        closes = [Decimal(100) + Decimal("0.1") * i for i in range(120)]
        closes[-1] = Decimal(117)  # breakout above the prior 20 highs
        return series(closes, H1, end, Decimal(10))

    def close(self) -> None:
        self.closed = True


def refreshed(failing: tuple[str, ...] = ()) -> IntradayResearchService:
    service = IntradayResearchService(FakeSource(failing))  # type: ignore[arg-type]
    asyncio.run(service.poll_once())
    return service


def test_all_pairs_ok_returns_candidates_for_every_research_symbol() -> None:
    snapshot = refreshed().snapshot()
    assert snapshot["status"] == "ok"
    assert snapshot["paper_only"] is True and snapshot["live_execution"] is False
    rows = snapshot["candidates"]
    assert [row["symbol"] for row in rows] == list(RESEARCH_SYMBOLS)  # type: ignore[union-attr]
    assert all(row["status"] == "ok" for row in rows)  # type: ignore[union-attr]
    assert all(row["candidate"]["action"] == "entry_review" for row in rows)  # type: ignore[index, union-attr]


def test_one_failed_pair_is_unavailable_and_others_stay_visible() -> None:
    snapshot = refreshed(failing=("SOL/USDT",)).snapshot()
    assert snapshot["status"] == "partial"
    by_symbol = {row["symbol"]: row for row in snapshot["candidates"]}  # type: ignore[union-attr]
    assert by_symbol["SOL/USDT"]["status"] == "unavailable"
    assert by_symbol["BTC/USDT"]["status"] == "ok"


def test_snapshot_expires_after_two_polls() -> None:
    service = refreshed()
    polled = service._polled_at_ms
    assert polled is not None
    assert service.snapshot(now_ms=polled + 2 * 3600 * 1000 + 1)["status"] == "stale"


def test_snapshot_before_first_poll_is_no_data() -> None:
    service = IntradayResearchService(FakeSource())  # type: ignore[arg-type]
    assert service.snapshot()["status"] == "no_data"


def test_clock_is_real_time_for_default_snapshot() -> None:
    service = refreshed()
    assert service.snapshot()["status"] == "ok"
    assert int(time() * 1000) >= (service._polled_at_ms or 0)
