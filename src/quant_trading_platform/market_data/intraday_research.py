"""Read-only intraday research snapshot: 4H trend context and 1H review candidates.

Separate from the daily SpotSignalService so the paper-trading path is untouched.
Each research symbol is fetched on its own; a failed pair is reported as unavailable
and never hides the others. Candidates expire when the snapshot is older than two polls.
"""

import asyncio
from dataclasses import asdict
from time import time

from quant_trading_platform.market_data.okx_candles import OKXCandleSource
from quant_trading_platform.strategies.intraday_trend import intraday_candidate
from quant_trading_platform.strategies.spot_momentum import (
    BAR_MS,
    RESEARCH_SYMBOLS,
    validate_candles,
)

BAR_4H = "4H"
BAR_1H = "1H"


class IntradayResearchService:
    def __init__(self, source: OKXCandleSource, interval_seconds: int = 3600) -> None:
        self.source = source
        self.interval_seconds = interval_seconds
        self._results: dict[str, dict[str, object]] = {}
        self._polled_at_ms: int | None = None

    def _fetch_candidate(self, symbol: str, now_ms: int) -> dict[str, object]:
        try:
            candles_4h = self.source.fetch(symbol, now_ms=now_ms, bar=BAR_4H)
            candles_1h = self.source.fetch(symbol, now_ms=now_ms, bar=BAR_1H)
            validate_candles(candles_4h, now_ms=now_ms, interval_ms=BAR_MS[BAR_4H])
            validate_candles(candles_1h, now_ms=now_ms, interval_ms=BAR_MS[BAR_1H])
            candidate = intraday_candidate(symbol, candles_4h, candles_1h)
        except (ValueError, OSError) as error:
            return {"symbol": symbol, "status": "unavailable",
                    "reason": f"Нет подтверждённых данных OKX: {error}"}
        return {"symbol": symbol, "status": "ok", "candidate": asdict(candidate)}

    async def poll_once(self) -> None:
        now_ms = int(time() * 1000)
        rows = await asyncio.gather(*(
            asyncio.to_thread(self._fetch_candidate, symbol, now_ms)
            for symbol in RESEARCH_SYMBOLS
        ))
        self._results = {str(row["symbol"]): row for row in rows}
        self._polled_at_ms = now_ms

    def snapshot(self, *, now_ms: int | None = None) -> dict[str, object]:
        current = int(time() * 1000) if now_ms is None else now_ms
        if self._polled_at_ms is None:
            return {"status": "no_data", "paper_only": True, "live_execution": False,
                    "candidates": [], "reason": "Ещё не было обновления исследования."}
        if current - self._polled_at_ms > 2 * self.interval_seconds * 1000:
            return {"status": "stale", "paper_only": True, "live_execution": False,
                    "candidates": [], "reason": "Снимок устарел: нужно обновить данные."}
        rows = [self._results[symbol] for symbol in RESEARCH_SYMBOLS if symbol in self._results]
        all_ok = all(row["status"] == "ok" for row in rows) and len(rows) == len(RESEARCH_SYMBOLS)
        return {"status": "ok" if all_ok else "partial", "paper_only": True,
                "live_execution": False, "candidates": rows,
                "reason": "Кандидаты для ручной проверки; заявки не создаются."}

