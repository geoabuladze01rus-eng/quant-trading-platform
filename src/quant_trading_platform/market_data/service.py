"""Bounded public polling owned by FastAPI lifespan; GET handlers never poll."""

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from time import time
from typing import Protocol

from quant_trading_platform.market_data.models import NormalizedOrderBook
from quant_trading_platform.models import MarketQuote, MarketType, Venue, normalize_symbol


class PublicBookSource(Protocol):
    venue: Venue

    def get_order_book(self, symbol: str) -> NormalizedOrderBook: ...
    def close(self) -> None: ...


@dataclass
class SourceState:
    status: str = "no_data"
    error: str | None = None
    book: NormalizedOrderBook | None = None


class MarketDataService:
    def __init__(
        self,
        sources: Sequence[PublicBookSource],
        cache: dict[tuple[str, str], MarketQuote],
        symbol: str = "BTC/USDT",
        interval_seconds: float = 1.0,
        max_age_ms: int = 1_000,
        clock: Callable[[], int] = lambda: int(time() * 1000),
    ) -> None:
        if interval_seconds < 0.25 or max_age_ms <= 0:
            raise ValueError("Invalid polling limits")
        self.sources = tuple(sources)
        self.cache = cache
        self.symbol = normalize_symbol(symbol)
        self.interval_seconds = interval_seconds
        self.max_age_ms = max_age_ms
        self.clock = clock
        self.states = {source.venue: SourceState() for source in sources}
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def poll_once(self) -> None:
        await asyncio.gather(*(self._poll(source) for source in self.sources))

    async def _poll(self, source: PublicBookSource) -> None:
        state = self.states[source.venue]
        key = (source.venue.value, self.symbol)
        try:
            book = await asyncio.to_thread(source.get_order_book, self.symbol)
            quote = book.to_quote()
            quote.validate()
            if (
                quote.venue != source.venue or normalize_symbol(quote.symbol) != self.symbol
                or quote.market_type != MarketType.CRYPTO
            ):
                raise ValueError("Source identity mismatch")
            age = self.clock() - min(book.timestamp_ms, book.received_at_ms)
            if age < 0 or max(book.timestamp_ms, book.received_at_ms) > self.clock():
                raise ValueError("Future timestamp")
            state.book = book
            if age > self.max_age_ms:
                state.status, state.error = "stale", "stale_market_data"
                self.cache.pop(key, None)
                return
            state.status, state.error = "ok", None
            self.cache[key] = quote
        except Exception as error:
            # Never expose upstream URLs, bodies, headers or credentials to callers.
            is_stale = getattr(error, "code", None) == "stale"
            state.status = "stale" if is_stale else "error"
            state.error = "stale_market_data" if is_stale else "public_market_data_unavailable"
            self.cache.pop(key, None)

    def snapshot(self) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for venue, state in self.states.items():
            book = state.book
            age = None if book is None else max(
                0, self.clock() - min(book.timestamp_ms, book.received_at_ms)
            )
            status = state.status
            if book is not None and max(book.timestamp_ms, book.received_at_ms) > self.clock():
                status = "error"
            if status == "ok" and age is not None and age > self.max_age_ms:
                status = "stale"
            result.append({
                "name": venue.value, "market": "crypto", "mode": "public_read_only",
                "status": status, "live_execution": False, "symbol": self.symbol,
                "data_age_ms": age, "error": state.error,
                "bid": None if book is None else str(book.to_quote().bid),
                "ask": None if book is None else str(book.to_quote().ask),
                "timestamp_source": None if book is None else book.timestamp_source,
            })
        return result

    async def _run(self) -> None:
        await asyncio.gather(*(self._run_source(source) for source in self.sources))

    async def _run_source(self, source: PublicBookSource) -> None:
        delay = self.interval_seconds
        while not self._stop.is_set():
            await self._poll(source)
            if self.states[source.venue].status == "error":
                delay = min(max(delay * 2, 1.0), 30.0)
            else:
                delay = self.interval_seconds
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="public-market-poll")

    async def stop(self) -> None:
        self._stop.set()
        try:
            if self._task is not None:
                await self._task
        finally:
            self._task = None
            for source in self.sources:
                source.close()
