"""Immutable, exact, venue-native public derivatives evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from time import time_ns
from typing import TYPE_CHECKING

from quant_trading_platform.models import Venue, normalize_symbol

if TYPE_CHECKING:
    from quant_trading_platform.connectors.crypto.derivatives import PublicDerivativesSource

SIGNAL_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")


def instrument_for(venue: Venue, symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    if symbol not in SIGNAL_SYMBOLS or venue not in (Venue.BINANCE, Venue.BYBIT, Venue.OKX):
        raise ValueError("Unsupported derivatives identity")
    return symbol.replace("/", "-") + "-SWAP" if venue == Venue.OKX else symbol.replace("/", "")


def exact_decimal(value: object, *, positive: bool = True) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise ValueError("Financial values require exact decimal input")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid financial value") from None
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("Financial value must be finite and positive")
    return result


@dataclass(frozen=True)
class DerivativesSnapshot:
    venue: Venue
    symbol: str
    instrument_id: str
    timestamp_ms: int
    received_at_ms: int
    mark_price: Decimal | None
    index_price: Decimal | None
    funding_rate: Decimal | None
    next_funding_time_ms: int | None
    open_interest: Decimal | None
    open_interest_unit: str | None
    source_fields: tuple[str, ...]


def normalize_derivatives_snapshot(
    *,
    venue: Venue,
    symbol: str,
    instrument_id: str,
    timestamp_ms: int,
    received_at_ms: int,
    mark_price: object = None,
    index_price: object = None,
    funding_rate: object = None,
    next_funding_time_ms: int | None = None,
    open_interest: object = None,
    open_interest_unit: str | None = None,
    source_fields: tuple[str, ...] = (),
) -> DerivativesSnapshot:
    symbol = normalize_symbol(symbol)
    if instrument_id != instrument_for(venue, symbol):
        raise ValueError("Derivatives instrument mismatch")
    if any(type(t) is not int or t <= 0 for t in (timestamp_ms, received_at_ms)):
        raise ValueError("Invalid timestamp")
    if timestamp_ms > received_at_ms:
        raise ValueError("Future source timestamp")
    if next_funding_time_ms is not None and (
        type(next_funding_time_ms) is not int or next_funding_time_ms <= 0
    ):
        raise ValueError("Invalid funding timestamp")
    oi = exact_decimal(open_interest)
    if oi is not None and not open_interest_unit:
        raise ValueError("Open interest requires native units")
    return DerivativesSnapshot(
        venue,
        symbol,
        instrument_id,
        timestamp_ms,
        received_at_ms,
        exact_decimal(mark_price),
        exact_decimal(index_price),
        exact_decimal(funding_rate, positive=False),
        next_funding_time_ms,
        oi,
        open_interest_unit,
        tuple(source_fields),
    )


@dataclass
class DerivativesSourceState:
    status: str = "no_data"
    error: str | None = None
    current: DerivativesSnapshot | None = None
    previous: DerivativesSnapshot | None = None


class DerivativesEvidenceService:
    def __init__(
        self,
        sources: Sequence[PublicDerivativesSource],
        symbol: str = "BTC/USDT",
        interval_seconds: float = 15,
        max_age_ms: int = 360_000,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
    ) -> None:
        if interval_seconds < 5 or max_age_ms <= 0:
            raise ValueError("Invalid derivatives polling limits")
        self.symbol = normalize_symbol(symbol)
        if self.symbol not in SIGNAL_SYMBOLS:
            raise ValueError("Unsupported evidence symbol")
        if len({s.venue for s in sources}) != len(sources):
            raise ValueError("Duplicate derivatives source")
        self.sources = tuple(sources)
        self.interval_seconds, self.max_age_ms, self.clock = interval_seconds, max_age_ms, clock
        self.states = {s.venue: DerivativesSourceState() for s in sources}
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def _status(self, state: DerivativesSourceState) -> str:
        current = state.current
        if state.status != "ok" or current is None:
            return state.status
        now = self.clock()
        if max(current.timestamp_ms, current.received_at_ms) > now:
            return "error"
        return (
            "stale"
            if now - min(current.timestamp_ms, current.received_at_ms) > self.max_age_ms
            else "ok"
        )

    async def _poll(self, source: PublicDerivativesSource) -> None:
        state = self.states[source.venue]
        try:
            current = await asyncio.to_thread(source.get_snapshot, self.symbol)
            # Revalidate even injected sources; adapters are not a trust boundary.
            validated = normalize_derivatives_snapshot(**asdict(current))
            if validated.venue != source.venue or validated.symbol != self.symbol:
                raise ValueError("Source identity mismatch")
            if max(current.timestamp_ms, current.received_at_ms) > self.clock():
                raise ValueError("Future evidence")
            previous = state.current if state.status == "ok" else None
            if previous is not None and (
                previous.instrument_id != current.instrument_id
                or previous.open_interest_unit != current.open_interest_unit
                or previous.timestamp_ms >= current.timestamp_ms
                or current.timestamp_ms - previous.timestamp_ms > self.max_age_ms
            ):
                previous = None
            state.current, state.previous = current, previous
            state.status, state.error = "ok", None
            state.status = self._status(state)
        except Exception:
            state.status, state.error = "error", "public_derivatives_unavailable"
            state.current, state.previous = None, None

    async def poll_once(self) -> None:
        await asyncio.gather(*(self._poll(source) for source in self.sources))

    def fresh_snapshot(self, venue: Venue, symbol: str) -> DerivativesSnapshot | None:
        if normalize_symbol(symbol) != self.symbol:
            return None
        state = self.states.get(venue)
        return state.current if state is not None and self._status(state) == "ok" else None

    def previous_snapshot(self, venue: Venue, symbol: str) -> DerivativesSnapshot | None:
        if self.fresh_snapshot(venue, symbol) is None:
            return None
        return self.states[venue].previous

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "name": venue.value,
                "symbol": self.symbol,
                "status": self._status(state),
                "error": state.error,
                "mode": "public_read_only",
                "live_execution": False,
            }
            for venue, state in self.states.items()
        ]

    async def _run_source(self, source: PublicDerivativesSource) -> None:
        delay = self.interval_seconds
        while not self._stop.is_set():
            await self._poll(source)
            delay = (
                min(delay * 2, 60)
                if self.states[source.venue].status == "error"
                else (self.interval_seconds)
            )
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), delay)

    async def _run(self) -> None:
        await asyncio.gather(*(self._run_source(s) for s in self.sources))

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="public-derivatives-poll")

    async def stop(self) -> None:
        self._stop.set()
        try:
            if self._task is not None:
                await self._task
        finally:
            self._task = None
            for source in self.sources:
                source.close()


class MultiDerivativesEvidenceService:
    def __init__(self, services: Sequence[DerivativesEvidenceService]) -> None:
        if not services or len({s.symbol for s in services}) != len(services):
            raise ValueError("Evidence symbols must be unique and nonempty")
        self.services = tuple(services)
        self.by_symbol = {s.symbol: s for s in services}

    def fresh_snapshot(self, venue: Venue, symbol: str) -> DerivativesSnapshot | None:
        service = self.by_symbol.get(normalize_symbol(symbol))
        return None if service is None else service.fresh_snapshot(venue, symbol)

    def previous_snapshot(self, venue: Venue, symbol: str) -> DerivativesSnapshot | None:
        service = self.by_symbol.get(normalize_symbol(symbol))
        return None if service is None else service.previous_snapshot(venue, symbol)

    def snapshot(self) -> list[dict[str, object]]:
        return [row for service in self.services for row in service.snapshot()]

    async def start(self) -> None:
        for service in self.services:
            await service.start()

    async def stop(self) -> None:
        await asyncio.gather(*(s.stop() for s in self.services))
