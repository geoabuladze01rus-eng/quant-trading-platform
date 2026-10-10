"""Background refresh of read-only OKX spot research signals.

The paper-trading core (SYMBOLS) and the research-only extras (RESEARCH_SYMBOLS) are
fetched independently: a failed extra pair is reported as missing and never blocks
the core pairs, while a failed core pair marks the whole snapshot unavailable.
"""

import asyncio
from contextlib import suppress
from dataclasses import asdict
from time import time

from quant_trading_platform.market_data.okx_candles import OKXCandleSource
from quant_trading_platform.strategies.spot_momentum import (
    RESEARCH_SYMBOLS,
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
        self.missing_symbols: tuple[str, ...] = RESEARCH_SYMBOLS
        self._series: dict[str, tuple[DailyCandle, ...]] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def _fetch_valid(self, symbol: str, now_ms: int) -> tuple[DailyCandle, ...] | None:
        try:
            candles = self.source.fetch(symbol, now_ms=now_ms)
            validate_candles(candles, now_ms=now_ms)
        except (ValueError, OSError):
            return None
        return candles

    async def poll_once(self) -> None:
        now_ms = int(time() * 1000)
        fetched = await asyncio.gather(*(
            asyncio.to_thread(self._fetch_valid, symbol, now_ms) for symbol in RESEARCH_SYMBOLS
        ))
        series = {
            symbol: candles
            for symbol, candles in zip(RESEARCH_SYMBOLS, fetched, strict=True)
            if candles is not None
        }
        self._series = series
        self.missing_symbols = tuple(s for s in RESEARCH_SYMBOLS if s not in series)
        # Core pairs must all be complete and share one candle date before paper use.
        core_ok = all(symbol in series for symbol in SYMBOLS)
        if core_ok:
            try:
                relative_strength_signals({s: series[s] for s in SYMBOLS}, SYMBOLS)
            except ValueError:
                core_ok = False
        self.status = "ok" if core_ok else "unavailable"

    def snapshot(self) -> dict[str, object]:
        if self.status != "ok":
            return {"status": self.status, "paper_only": True, "live_execution": False,
                    "signals": [], "missing_symbols": list(self.missing_symbols),
                    "reason": "Нет подтверждённых дневных данных OKX."}
        try:
            now_ms = int(time() * 1000)
            for candles in self._series.values():
                validate_candles(candles, now_ms=now_ms)
            present = tuple(s for s in RESEARCH_SYMBOLS if s in self._series)
            signals = [trend_signal(symbol, self._series[symbol]) for symbol in present]
            signals.extend(relative_strength_signals(
                {s: self._series[s] for s in present}, present,
            ))
        except ValueError:
            return {"status": "stale", "paper_only": True, "live_execution": False,
                    "signals": [], "missing_symbols": list(self.missing_symbols),
                    "reason": "Свечи устарели или не совпадают по дате."}
        return {"status": "ok", "paper_only": True, "live_execution": False,
                "signals": [asdict(signal) for signal in signals],
                "missing_symbols": list(self.missing_symbols),
                "reason": "Кандидаты для исследования; заявки не создаются."}

    def completed_series(self, *, now_ms: int) -> dict[str, tuple[DailyCandle, ...]]:
        """Detached validated inputs for the opt-in paper executor: core pairs only."""
        if self.status != "ok" or any(symbol not in self._series for symbol in SYMBOLS):
            raise ValueError("Completed candles unavailable")
        series = {symbol: self._series[symbol] for symbol in SYMBOLS}
        for candles in series.values():
            validate_candles(candles, now_ms=now_ms)
        relative_strength_signals(series, SYMBOLS)
        return series

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
