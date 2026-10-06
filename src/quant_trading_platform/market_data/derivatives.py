"""Validated derivatives snapshots and polling for read-only signal evidence."""

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from time import time
from typing import Protocol, TypeAlias

from quant_trading_platform.market_data.models import StaleMarketDataError
from quant_trading_platform.models import Venue, normalize_symbol

DecimalInput: TypeAlias = Decimal | str | int
_SIGNAL_SYMBOLS = frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"})


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


def derivatives_instrument_id(venue: Venue, symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if normalized not in _SIGNAL_SYMBOLS:
        raise ValueError("Unsupported derivatives signal symbol")
    if venue in (Venue.BINANCE, Venue.BYBIT):
        return normalized.replace("/", "")
    if venue == Venue.OKX:
        return f"{normalized.replace('/', '-')}-SWAP"
    raise ValueError("Unsupported derivatives venue")


def _decimal(
    value: DecimalInput | None,
    *,
    name: str,
    positive: bool,
) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{name} must be finite") from error
    if not parsed.is_finite():
        qualifier = "finite and positive" if positive else "finite"
        raise ValueError(f"{name} must be {qualifier}")
    if positive and parsed <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return parsed


def normalize_derivatives_snapshot(
    *,
    venue: Venue,
    symbol: str,
    instrument_id: str,
    timestamp_ms: int,
    received_at_ms: int,
    mark_price: DecimalInput | None,
    index_price: DecimalInput | None,
    funding_rate: DecimalInput | None,
    next_funding_time_ms: int | None,
    open_interest: DecimalInput | None,
    open_interest_unit: str | None,
    source_fields: tuple[str, ...],
    max_age_ms: int,
) -> DerivativesSnapshot:
    normalized_symbol = normalize_symbol(symbol)
    expected_instrument = derivatives_instrument_id(venue, normalized_symbol)
    if instrument_id != expected_instrument:
        raise ValueError("Derivatives instrument identity mismatch")
    if any(type(value) is not int or value < 0 for value in (timestamp_ms, received_at_ms)):
        raise ValueError("Invalid derivatives timestamp")
    if type(max_age_ms) is not int or max_age_ms <= 0:
        raise ValueError("max_age_ms must be positive")
    if timestamp_ms > received_at_ms:
        raise ValueError("Future derivatives timestamp")
    if received_at_ms - timestamp_ms > max_age_ms:
        raise StaleMarketDataError("Stale derivatives data")
    if next_funding_time_ms is not None and (
        type(next_funding_time_ms) is not int or next_funding_time_ms < 0
    ):
        raise ValueError("Invalid next funding timestamp")

    mark = _decimal(mark_price, name="mark_price", positive=True)
    index = _decimal(index_price, name="index_price", positive=True)
    funding = _decimal(funding_rate, name="funding_rate", positive=False)
    interest = _decimal(open_interest, name="open_interest", positive=True)
    unit = None if open_interest_unit is None else open_interest_unit.strip()
    if interest is not None and not unit:
        raise ValueError("Open interest unit is required")
    if interest is None and unit:
        raise ValueError("Open interest unit requires open interest")

    return DerivativesSnapshot(
        venue=venue,
        symbol=normalized_symbol,
        instrument_id=instrument_id,
        timestamp_ms=timestamp_ms,
        received_at_ms=received_at_ms,
        mark_price=mark,
        index_price=index,
        funding_rate=funding,
        next_funding_time_ms=next_funding_time_ms,
        open_interest=interest,
        open_interest_unit=unit,
        source_fields=tuple(source_fields),
    )



class DerivativesSource(Protocol):
    venue: Venue

    def get_snapshot(self, symbol: str) -> DerivativesSnapshot: ...
    def close(self) -> None: ...


@dataclass
class DerivativesSourceState:
    status: str = "no_data"
    error: str | None = None
    snapshot: DerivativesSnapshot | None = None
    previous_snapshot: DerivativesSnapshot | None = None


class DerivativesEvidenceService:
    def __init__(
        self,
        sources: Sequence[DerivativesSource],
        *,
        symbol: str,
        interval_seconds: float = 5.0,
        max_age_ms: int = 10_000,
        clock: Callable[[], int] = lambda: int(time() * 1000),
    ) -> None:
        if interval_seconds < 0.25 or max_age_ms <= 0:
            raise ValueError("Invalid derivatives polling limits")
        self.sources = tuple(sources)
        self.symbol = normalize_symbol(symbol)
        if self.symbol not in _SIGNAL_SYMBOLS:
            raise ValueError("Unsupported derivatives signal symbol")
        self.interval_seconds = interval_seconds
        self.max_age_ms = max_age_ms
        self.clock = clock
        self.states = {source.venue: DerivativesSourceState() for source in sources}
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def poll_once(self) -> None:
        await asyncio.gather(*(self._poll(source) for source in self.sources))

    @staticmethod
    def _comparable(
        previous: DerivativesSnapshot,
        current: DerivativesSnapshot,
    ) -> bool:
        return (
            previous.venue == current.venue
            and previous.symbol == current.symbol
            and previous.instrument_id == current.instrument_id
            and previous.open_interest is not None
            and current.open_interest is not None
            and previous.open_interest_unit == current.open_interest_unit
        )

    async def _poll(self, source: DerivativesSource) -> None:
        state = self.states[source.venue]
        try:
            current = await asyncio.to_thread(source.get_snapshot, self.symbol)
            now = self.clock()
            if current.venue != source.venue or current.symbol != self.symbol:
                raise ValueError("Derivatives source identity mismatch")
            if current.timestamp_ms > now or current.received_at_ms > now:
                raise ValueError("Future derivatives data")
            age = now - min(current.timestamp_ms, current.received_at_ms)
            if age > self.max_age_ms:
                state.status, state.error = "stale", "stale_derivatives_data"
                return
            previous = state.snapshot
            state.previous_snapshot = (
                previous
                if previous is not None and self._comparable(previous, current)
                else None
            )
            state.snapshot = current
            state.status, state.error = "ok", None
        except Exception as error:
            is_stale = getattr(error, "code", None) == "stale"
            state.status = "stale" if is_stale else "error"
            state.error = (
                "stale_derivatives_data"
                if is_stale
                else "public_derivatives_data_unavailable"
            )

    def _effective_status(
        self,
        state: DerivativesSourceState,
    ) -> tuple[str, int | None]:
        current = state.snapshot
        if current is None:
            return state.status, None
        now = self.clock()
        if current.timestamp_ms > now or current.received_at_ms > now:
            return "error", None
        age = max(0, now - min(current.timestamp_ms, current.received_at_ms))
        if state.status == "ok" and age > self.max_age_ms:
            return "stale", age
        return state.status, age

    def fresh_snapshot(
        self,
        venue: Venue,
        symbol: str,
    ) -> DerivativesSnapshot | None:
        if normalize_symbol(symbol) != self.symbol:
            return None
        state = self.states.get(venue)
        if state is None:
            return None
        status, _ = self._effective_status(state)
        return state.snapshot if status == "ok" else None

    def snapshot(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for venue, state in self.states.items():
            status, age = self._effective_status(state)
            current = state.snapshot
            rows.append({
                "name": venue.value,
                "symbol": self.symbol,
                "market": "crypto_derivatives",
                "mode": "public_read_only",
                "status": status,
                "data_age_ms": age,
                "error": state.error,
                "instrument_id": None if current is None else current.instrument_id,
                "mark_price": None if current is None else str(current.mark_price),
                "index_price": None if current is None else str(current.index_price),
                "funding_rate": None if current is None else str(current.funding_rate),
                "open_interest": None if current is None else str(current.open_interest),
                "open_interest_unit": (
                    None if current is None else current.open_interest_unit
                ),
                "live_execution": False,
            })
        return rows

    async def _run_source(self, source: DerivativesSource) -> None:
        delay = self.interval_seconds
        while not self._stop.is_set():
            await self._poll(source)
            if self.states[source.venue].status == "error":
                delay = min(max(delay * 2, 1.0), 30.0)
            else:
                delay = self.interval_seconds
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)

    async def _run(self) -> None:
        await asyncio.gather(*(self._run_source(source) for source in self.sources))

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(
                self._run(),
                name=f"public-derivatives-poll:{self.symbol}",
            )

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
        if not services or len({service.symbol for service in services}) != len(services):
            raise ValueError("Derivatives data symbols must be unique and nonempty")
        self.services = tuple(services)
        self.by_symbol = {service.symbol: service for service in services}

    def fresh_snapshot(
        self,
        venue: Venue,
        symbol: str,
    ) -> DerivativesSnapshot | None:
        service = self.by_symbol.get(normalize_symbol(symbol))
        return None if service is None else service.fresh_snapshot(venue, symbol)

    def snapshot(self) -> list[dict[str, object]]:
        return [row for service in self.services for row in service.snapshot()]

    async def start(self) -> None:
        for service in self.services:
            await service.start()

    async def stop(self) -> None:
        await asyncio.gather(*(service.stop() for service in self.services))
