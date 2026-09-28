"""Bounded public polling owned by FastAPI lifespan; GET handlers never poll."""

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from time import time
from typing import Protocol

from quant_trading_platform.crypto_universe import SpotInstrumentRules
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
    rules: SpotInstrumentRules | None = None


class MarketDataService:
    def __init__(
        self,
        sources: Sequence[PublicBookSource],
        cache: dict[tuple[str, str], MarketQuote],
        symbol: str | Sequence[str] = "BTC/USDT",
        interval_seconds: float = 1.0,
        max_age_ms: int = 1_000,
        clock: Callable[[], int] = lambda: int(time() * 1000),
        on_update: Callable[[], None] | None = None,
        require_instrument_rules: bool = False,
        instrument_rules_refresh_ms: int = 3_600_000,
    ) -> None:
        if (
            interval_seconds < 0.25
            or max_age_ms <= 0
            or instrument_rules_refresh_ms <= 0
        ):
            raise ValueError("Invalid polling limits")
        self.sources = tuple(sources)
        self.cache = cache
        configured_symbols = (symbol,) if isinstance(symbol, str) else symbol
        self.symbols = tuple(
            dict.fromkeys(normalize_symbol(configured) for configured in configured_symbols)
        )
        if not self.symbols:
            raise ValueError("At least one market data symbol is required")
        self.symbol = self.symbols[0]
        self.interval_seconds = interval_seconds
        self.max_age_ms = max_age_ms
        self.clock = clock
        self.on_update = on_update
        self.require_instrument_rules = require_instrument_rules
        self.instrument_rules_refresh_ms = instrument_rules_refresh_ms
        self.pair_states: dict[tuple[Venue, str], SourceState] = {}
        self.states: dict[Venue | tuple[Venue, str], SourceState] = {}
        for source in self.sources:
            for configured in self.symbols:
                state = SourceState()
                self.pair_states[(source.venue, configured)] = state
                self.states[(source.venue, configured)] = state
                if configured == self.symbol:
                    # Compatibility for callers that inspect the original one-symbol view.
                    self.states[source.venue] = state
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._sources_closed = False

    async def poll_once(self) -> None:
        await asyncio.gather(*(
            self._poll(source, symbol)
            for source in self.sources
            for symbol in self.symbols
        ))

    async def _poll(self, source: PublicBookSource, symbol: str) -> None:
        state = self.pair_states[(source.venue, symbol)]
        key = (source.venue.value, symbol)
        try:
            loader = getattr(source, "get_instrument_rules", None)
            rules_expired = (
                state.rules is None
                or self.clock() - state.rules.timestamp_ms >= self.instrument_rules_refresh_ms
            )
            if rules_expired and callable(loader):
                rules = await asyncio.to_thread(loader, symbol)
                if not isinstance(rules, SpotInstrumentRules):
                    raise ValueError("Invalid public instrument rules")
                rules.validate()
                if rules.venue != source.venue or rules.symbol != symbol:
                    raise ValueError("Instrument rules identity mismatch")
                if rules.timestamp_ms > self.clock():
                    raise ValueError("Future instrument rules timestamp")
                state.rules = rules
            if self.require_instrument_rules and state.rules is None:
                raise ValueError("Public instrument rules are unavailable")
            book = await asyncio.to_thread(source.get_order_book, symbol)
            quote = book.to_quote()
            quote.validate()
            if (
                quote.venue != source.venue or normalize_symbol(quote.symbol) != symbol
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
            else:
                state.status, state.error = "ok", None
                self.cache[key] = quote
        except Exception as error:
            # Never expose upstream URLs, bodies, headers or credentials to callers.
            is_stale = getattr(error, "code", None) == "stale"
            state.status = "stale" if is_stale else "error"
            state.error = "stale_market_data" if is_stale else "public_market_data_unavailable"
            self.cache.pop(key, None)

        if self.on_update is not None:
            try:
                self.on_update()
            except Exception:
                # A failed audit/strategy callback must stop this pair, not silently
                # kill its polling task or authorize execution from the cached book.
                state.status, state.error = "error", "market_update_processing_failed"
                self.cache.pop(key, None)

    def book_for_simulation(self, venue: Venue, symbol: str) -> NormalizedOrderBook | None:
        """Never return a last-good book after a source error; engine rechecks age."""
        normalized = normalize_symbol(symbol)
        state = self.pair_states.get((venue, normalized))
        if state is None or state.status not in ("ok", "stale"):
            return None
        book = state.book
        if book is None or book.symbol != normalized:
            return None
        return book

    def rules_for_simulation(self, venue: Venue, symbol: str) -> SpotInstrumentRules | None:
        normalized = normalize_symbol(symbol)
        state = self.pair_states.get((venue, normalized))
        if state is None or state.status != "ok":
            return None
        rules = state.rules
        age = None if rules is None else self.clock() - rules.timestamp_ms
        if rules is None or age is None or age < 0 or age >= self.instrument_rules_refresh_ms:
            return None
        return rules

    def snapshot(self) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for source in self.sources:
            for symbol in self.symbols:
                state = self.pair_states[(source.venue, symbol)]
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
                    "name": source.venue.value, "market": "crypto", "mode": "public_read_only",
                    "status": status, "live_execution": False, "symbol": symbol,
                    "data_age_ms": age, "error": state.error,
                    "bid": None if book is None else str(book.to_quote().bid),
                    "ask": None if book is None else str(book.to_quote().ask),
                    "timestamp_source": None if book is None else book.timestamp_source,
                    "depth_status": "available" if status == "ok" else "unavailable",
                    "bid_levels": 0 if book is None else len(book.bids),
                    "ask_levels": 0 if book is None else len(book.asks),
                    "base_asset": None if state.rules is None else state.rules.base_asset,
                    "quote_asset": None if state.rules is None else state.rules.quote_asset,
                    "market_type": "spot",
                    "instrument_status": (
                        "unavailable" if state.rules is None else state.rules.status
                    ),
                    "tick_size": None if state.rules is None else str(state.rules.tick_size),
                    "quantity_step": (
                        None if state.rules is None else str(state.rules.quantity_step)
                    ),
                    "min_quantity": (
                        None if state.rules is None else str(state.rules.min_quantity)
                    ),
                    "min_notional": (
                        None
                        if state.rules is None or state.rules.min_notional is None
                        else str(state.rules.min_notional)
                    ),
                    "instrument_rules_source": (
                        None if state.rules is None else state.rules.source
                    ),
                })
        return result

    async def _run(self) -> None:
        await asyncio.gather(*(
            self._run_source(source, symbol)
            for source in self.sources
            for symbol in self.symbols
        ))

    async def _run_source(self, source: PublicBookSource, symbol: str) -> None:
        delay = self.interval_seconds
        while not self._stop.is_set():
            await self._poll(source, symbol)
            if self.pair_states[(source.venue, symbol)].status == "error":
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
            if not self._sources_closed:
                for source in self.sources:
                    source.close()
                self._sources_closed = True
