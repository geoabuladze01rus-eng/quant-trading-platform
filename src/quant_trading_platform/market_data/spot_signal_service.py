"""Background refresh of read-only OKX spot research signals."""

import asyncio
from contextlib import suppress
from dataclasses import asdict
from time import time

from quant_trading_platform.market_data.okx_candles import OKXCandleSource
from quant_trading_platform.strategies.spot_momentum import (
    SYMBOLS,
    DailyCandle,
    relative_strength_signals,
    trend_signal,
    validate_candles,
)


class SpotSignalService:
    def __init__(self, source: OKXCandleSource, interval_seconds: int = 3600) -> None:
        self.source = source
        self.interval_seconds = interval_seconds
        self.status = "no_data"
        self._series: dict[str, tuple[DailyCandle, ...]] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def poll_once(self) -> None:
        now_ms = int(time() * 1000)
        try:
            series = dict(zip(
                SYMBOLS,
                await asyncio.gather(*(
                    asyncio.to_thread(self.source.fetch, symbol, now_ms=now_ms)
                    for symbol in SYMBOLS
                )),
                strict=True,
            ))
            for candles in series.values():
                validate_candles(candles, now_ms=now_ms)
            # All three pairs must be complete before publishing any candidate.
            relative_strength_signals(series)
        except (ValueError, OSError):
            self._series = {}
            self.status = "unavailable"
            return
        self._series = series
        self.status = "ok"

    def snapshot(self) -> dict[str, object]:
        if self.status != "ok":
            return {"status": self.status, "paper_only": True, "live_execution": False,
                    "signals": [], "reason": "Нет подтверждённых дневных данных OKX."}
        try:
            now_ms = int(time() * 1000)
            for candles in self._series.values():
                validate_candles(candles, now_ms=now_ms)
            signals = [trend_signal(symbol, self._series[symbol]) for symbol in SYMBOLS]
            signals.extend(relative_strength_signals(self._series))
        except ValueError:
            return {"status": "stale", "paper_only": True, "live_execution": False,
                    "signals": [], "reason": "Свечи устарели или не совпадают по дате."}
        return {"status": "ok", "paper_only": True, "live_execution": False,
                "signals": [asdict(signal) for signal in signals],
                "reason": "Кандидаты для исследования; заявки не создаются."}

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.poll_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="okx-spot-research")

    async def stop(self) -> None:
        self._stop.set()
        try:
            if self._task is not None:
                await self._task
        finally:
            self._task = None
            self._series = {}
            self.source.close()
