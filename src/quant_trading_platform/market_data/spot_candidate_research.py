"""Read-only spot candidate research: 1D, 4H, 1H and 15m candles run through the v6.0 rules.

Nothing here sends a Telegram message or creates an order. The news filter is not
connected, so every candidate is built with news_checked=False and cannot be
deliverable. Each symbol is fetched on its own; a failed pair is reported as
unavailable and never hides the others.
"""

import asyncio
from time import time
from typing import Protocol

from quant_trading_platform.strategies.spot_momentum import RESEARCH_SYMBOLS, DailyCandle
from quant_trading_platform.strategies.spot_signal_generator import (
    CandidateResult,
    build_candidate,
)

BARS = ("1Dutc", "4H", "1H", "15m")
NEWS_CHECKED = False
SNAPSHOT_INTERVAL_SECONDS = 3600


class CandleSource(Protocol):
    def fetch(self, symbol: str, *, now_ms: int, bar: str = "1Dutc") -> tuple[DailyCandle, ...]:
        ...


def _row(result: CandidateResult) -> dict[str, object]:
    plan = result.plan
    return {
        "symbol": result.symbol,
        "status": "ok",
        "grade": result.evaluation.grade,
        "deliverable": result.evaluation.deliverable,
        "net_risk_reward": result.evaluation.net_risk_reward,
        "current_price": result.current_price,
        "reasons": list(result.evaluation.reasons),
        "plan": None if plan is None else {
            "entry_low": plan.entry_low,
            "entry_high": plan.entry_high,
            "stop_loss": plan.stop_loss,
            "targets": list(plan.targets),
        },
    }


class SpotCandidateResearchService:
    def __init__(self, source: CandleSource, interval_seconds: int = SNAPSHOT_INTERVAL_SECONDS):
        self.source = source
        self.interval_seconds = interval_seconds
        self._results: dict[str, dict[str, object]] = {}
        self._polled_at_ms: int | None = None

    def _research_one(self, symbol: str, now_ms: int) -> dict[str, object]:
        try:
            series = {bar: self.source.fetch(symbol, now_ms=now_ms, bar=bar) for bar in BARS}
        except (ValueError, OSError) as error:
            return {
                "symbol": symbol,
                "status": "unavailable",
                "grade": "NONE",
                "deliverable": False,
                "reasons": [f"Нет подтверждённых данных OKX: {error}"],
            }
        result = build_candidate(
            symbol,
            candles_1d=series["1Dutc"],
            candles_4h=series["4H"],
            candles_1h=series["1H"],
            candles_15m=series["15m"],
            now_ms=now_ms,
            news_checked=NEWS_CHECKED,
        )
        return _row(result)

    async def poll_once(self, now_ms: int | None = None) -> None:
        current = int(time() * 1000) if now_ms is None else now_ms
        rows = await asyncio.gather(*(
            asyncio.to_thread(self._research_one, symbol, current)
            for symbol in RESEARCH_SYMBOLS
        ))
        self._results = {str(row["symbol"]): row for row in rows}
        self._polled_at_ms = current

    def snapshot(self, *, now_ms: int | None = None) -> dict[str, object]:
        current = int(time() * 1000) if now_ms is None else now_ms
        base = {
            "paper_only": True,
            "live_execution": False,
            "news_checked": NEWS_CHECKED,
        }
        if self._polled_at_ms is None:
            return {**base, "status": "no_data", "candidates": [], "deliverable_count": 0,
                    "reason": "Ещё не было обновления исследования."}
        if current - self._polled_at_ms > 2 * self.interval_seconds * 1000:
            return {**base, "status": "stale", "candidates": [], "deliverable_count": 0,
                    "reason": "Снимок устарел: нужно обновить данные."}
        rows = [self._results[s] for s in RESEARCH_SYMBOLS if s in self._results]
        all_ok = len(rows) == len(RESEARCH_SYMBOLS) and all(r["status"] == "ok" for r in rows)
        deliverable = sum(1 for r in rows if r["deliverable"] is True)
        return {
            **base,
            "status": "ok" if all_ok else "partial",
            "candidates": rows,
            "deliverable_count": deliverable,
            "reason": (
                "Исследование без новостного фильтра: сигналы не отправляются, заявки не создаются."
            ),
        }
