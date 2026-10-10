import asyncio
from decimal import Decimal
from time import time

import pytest

from quant_trading_platform.market_data.spot_signal_service import SpotSignalService
from quant_trading_platform.strategies.spot_momentum import (
    DAY_MS,
    RESEARCH_SYMBOLS,
    SYMBOLS,
    DailyCandle,
    relative_strength_signals,
)


def last_completed_day_ms() -> int:
    return (int(time() * 1000) // DAY_MS - 1) * DAY_MS


def make_candles(end_ts: int, base: int = 100, count: int = 130) -> tuple[DailyCandle, ...]:
    return tuple(
        DailyCandle(
            end_ts - (count - 1 - index) * DAY_MS,
            Decimal(base + index), Decimal(base + index + 1), Decimal(base + index - 1),
            Decimal(base + index), Decimal(1),
        )
        for index in range(count)
    )


class FakeSource:
    def __init__(self, failing: tuple[str, ...] = ()) -> None:
        self.failing = set(failing)
        self.closed = False

    def fetch(self, symbol: str, *, now_ms: int) -> tuple[DailyCandle, ...]:
        if symbol in self.failing:
            raise ValueError("OKX public daily candles unavailable")
        return make_candles(last_completed_day_ms())

    def close(self) -> None:
        self.closed = True


def refreshed(failing: tuple[str, ...] = ()) -> SpotSignalService:
    service = SpotSignalService(FakeSource(failing))  # type: ignore[arg-type]
    asyncio.run(service.poll_once())
    return service


def test_research_universe_adds_sol_and_keeps_paper_universe() -> None:
    assert "SOL/USDT" in RESEARCH_SYMBOLS
    assert SYMBOLS == ("BTC/USDT", "ETH/USDT", "LTC/USDT")


def test_default_ranking_still_requires_paper_universe() -> None:
    series = {symbol: make_candles(last_completed_day_ms()) for symbol in RESEARCH_SYMBOLS}
    with pytest.raises(ValueError):
        relative_strength_signals(series)


def test_sol_failure_does_not_block_paper_core_pairs() -> None:
    service = refreshed(failing=("SOL/USDT",))
    assert service.status == "ok"
    assert service.missing_symbols == ("SOL/USDT",)
    snapshot = service.snapshot()
    assert snapshot["status"] == "ok"
    assert snapshot["missing_symbols"] == ["SOL/USDT"]
    assert {s["symbol"] for s in snapshot["signals"]} == set(SYMBOLS)  # type: ignore[index, union-attr]
    assert set(service.completed_series(now_ms=int(time() * 1000))) == set(SYMBOLS)


def test_sol_available_is_research_only() -> None:
    service = refreshed()
    assert service.missing_symbols == ()
    snapshot = service.snapshot()
    assert "SOL/USDT" in {s["symbol"] for s in snapshot["signals"]}  # type: ignore[index, union-attr]
    # Paper executor input never includes SOL, so no paper balance or order can be created.
    assert set(service.completed_series(now_ms=int(time() * 1000))) == set(SYMBOLS)


def test_core_pair_failure_marks_everything_unavailable() -> None:
    service = refreshed(failing=("LTC/USDT",))
    assert service.status == "unavailable"
    assert service.snapshot()["signals"] == []
    with pytest.raises(ValueError):
        service.completed_series(now_ms=int(time() * 1000))
