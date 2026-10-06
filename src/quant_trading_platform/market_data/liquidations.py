"""Bounded, partial public liquidation observations; native sizes are never summed."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from decimal import Decimal
from threading import RLock
from time import monotonic, time_ns
from typing import Protocol, cast

import websockets

from quant_trading_platform.market_data.derivatives import (
    SIGNAL_SYMBOLS,
    exact_decimal,
    instrument_for,
)
from quant_trading_platform.models import Venue, normalize_symbol


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Malformed liquidation data")
    return cast(dict[str, object], value)


def _timestamp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Invalid timestamp")
    return int(value)


@dataclass(frozen=True)
class LiquidationEvent:
    venue: Venue
    symbol: str
    timestamp_ms: int
    side: str
    quantity: Decimal
    price: Decimal | None
    quantity_unit: str
    source_completeness: str


def _event(
    venue: Venue,
    instrument: object,
    timestamp: object,
    side: str,
    quantity: object,
    price: object,
    now_ms: int,
) -> LiquidationEvent:
    if not isinstance(instrument, str):
        raise ValueError("Missing instrument")
    symbol = normalize_symbol(instrument.removesuffix("-SWAP"))
    if instrument_for(venue, symbol) != instrument or side not in ("long", "short"):
        raise ValueError("Invalid liquidation identity/side")
    ts = _timestamp(timestamp)
    if ts <= 0 or ts > now_ms:
        raise ValueError("Invalid liquidation timestamp")
    size = exact_decimal(quantity)
    if size is None:
        raise ValueError("Missing liquidation quantity")
    return LiquidationEvent(
        venue,
        symbol,
        ts,
        side,
        size,
        exact_decimal(price),
        "contracts" if venue == Venue.OKX else "base_asset",
        "partial_exchange_stream",
    )


def parse_binance_liquidation(
    payload: object, *, now_ms: int | None = None
) -> tuple[LiquidationEvent, ...]:
    now = time_ns() // 1_000_000 if now_ms is None else now_ms
    if isinstance(payload, list):
        return tuple(e for row in payload for e in parse_binance_liquidation(row, now_ms=now))
    data = _object(payload)
    data = _object(data.get("data", data))
    if data.get("e") != "forceOrder":
        raise ValueError("Unexpected public event")
    row = _object(data.get("o"))
    side = {"SELL": "long", "BUY": "short"}.get(str(row.get("S")), "invalid")
    return (
        _event(
            Venue.BINANCE,
            row.get("s"),
            row.get("T"),
            side,
            row.get("q"),
            row.get("ap", row.get("p")),
            now,
        ),
    )


def parse_bybit_liquidation(
    payload: object, *, now_ms: int | None = None
) -> tuple[LiquidationEvent, ...]:
    now = time_ns() // 1_000_000 if now_ms is None else now_ms
    data = _object(payload)
    rows = data.get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Missing liquidation events")
    result = []
    for raw in rows:
        row = _object(raw)
        if data.get("topic") != f"allLiquidation.{row.get('s')}":
            raise ValueError("Topic identity mismatch")
        # Bybit S is the liquidated position side; Buy is a long liquidation.
        side = {"Buy": "long", "Sell": "short"}.get(str(row.get("S")), "invalid")
        result.append(
            _event(Venue.BYBIT, row.get("s"), row.get("T"), side, row.get("v"), row.get("p"), now)
        )
    return tuple(result)


def parse_okx_liquidation(
    payload: object, *, now_ms: int | None = None
) -> tuple[LiquidationEvent, ...]:
    now = time_ns() // 1_000_000 if now_ms is None else now_ms
    data = _object(payload)
    arg = _object(data.get("arg"))
    if arg.get("channel") != "liquidation-orders" or arg.get("instType") != "SWAP":
        raise ValueError("Unexpected public channel")
    rows = data.get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Missing liquidation events")
    result = []
    for raw in rows:
        row = _object(raw)
        if row.get("instType") != "SWAP":
            raise ValueError("Invalid contract type")
        details = row.get("details")
        if not isinstance(details, list) or not details:
            raise ValueError("Missing liquidation details")
        for raw_detail in details:
            detail = _object(raw_detail)
            side = {"sell": "long", "buy": "short"}.get(str(detail.get("side")), "invalid")
            if detail.get("posSide") not in (side, "net"):
                raise ValueError("Liquidation side mismatch")
            result.append(
                _event(
                    Venue.OKX,
                    row.get("instId"),
                    detail.get("ts"),
                    side,
                    detail.get("sz"),
                    detail.get("bkPx"),
                    now,
                )
            )
    return tuple(result)


class LiquidationWindow:
    def __init__(self, max_events: int = 10_000) -> None:
        if max_events <= 0:
            raise ValueError("Retention must be positive")
        self.events: deque[LiquidationEvent] = deque(maxlen=max_events)
        self._seen: set[LiquidationEvent] = set()
        self._lock = RLock()

    def add(self, event: LiquidationEvent, *, now_ms: int | None = None) -> bool:
        with self._lock:
            return self._add(event, now_ms=now_ms)

    def _add(self, event: LiquidationEvent, *, now_ms: int | None = None) -> bool:
        now = time_ns() // 1_000_000 if now_ms is None else now_ms
        if event.timestamp_ms > now or event.timestamp_ms <= 0:
            raise ValueError("Invalid liquidation timestamp")
        if event.timestamp_ms < now - 3_600_000 or event in self._seen:
            return False
        # Validate injected events too, without relabeling the native unit.
        expected = _event(
            event.venue,
            instrument_for(event.venue, event.symbol),
            event.timestamp_ms,
            event.side,
            event.quantity,
            event.price,
            now,
        )
        if event != expected:
            raise ValueError("Invalid normalized liquidation")
        if len(self.events) == self.events.maxlen:
            self._seen.discard(self.events[0])
        self.events.append(event)
        self._seen.add(event)
        return True

    def summary(self, symbol: str, now_ms: int) -> dict[str, object]:
        symbol = normalize_symbol(symbol)
        with self._lock:
            retained = tuple(self.events)
        result: dict[str, object] = {}
        for name, duration in (
            ("window_5m", 300_000),
            ("window_15m", 900_000),
            ("window_1h", 3_600_000),
        ):
            venues = []
            for venue in (Venue.BINANCE, Venue.BYBIT, Venue.OKX):
                events = [
                    e
                    for e in retained
                    if e.venue == venue
                    and e.symbol == symbol
                    and now_ms - duration <= e.timestamp_ms <= now_ms
                ]
                if not events:
                    continue
                venues.append(
                    {
                        "venue": venue.value,
                        "event_count": len(events),
                        "long_quantity": str(
                            sum((e.quantity for e in events if e.side == "long"), Decimal(0))
                        ),
                        "short_quantity": str(
                            sum((e.quantity for e in events if e.side == "short"), Decimal(0))
                        ),
                        "quantity_unit": events[0].quantity_unit,
                        "largest_event_quantity": str(max(e.quantity for e in events)),
                        "latest_event_timestamp_ms": max(e.timestamp_ms for e in events),
                        "source_completeness": "partial_exchange_stream",
                    }
                )
            result[name] = {"venues": venues, "comparable": False, "cross_venue_notional": None}
        return result


def parse_supported_liquidations(
    venue: Venue,
    payload: object,
    now_ms: int,
) -> tuple[LiquidationEvent, ...]:
    """Filter unrelated all-market instruments without weakening supported validation."""
    allowed = {instrument_for(venue, symbol) for symbol in SIGNAL_SYMBOLS}
    if venue == Venue.BINANCE:
        if isinstance(payload, list):
            return tuple(
                event
                for row in payload
                for event in parse_supported_liquidations(venue, row, now_ms)
            )
        data = _object(payload)
        data = _object(data.get("data", data))
        row = _object(data.get("o"))
        if isinstance(row.get("s"), str) and row["s"] not in allowed:
            return ()
        return parse_binance_liquidation(payload, now_ms=now_ms)
    if venue == Venue.OKX:
        data = _object(payload)
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ValueError("Missing liquidation events")
        supported = []
        for raw in rows:
            row = _object(raw)
            if isinstance(row.get("instId"), str) and row["instId"] not in allowed:
                continue
            supported.append(row)
        if not supported:
            return ()
        return parse_okx_liquidation({**data, "data": supported}, now_ms=now_ms)
    return parse_bybit_liquidation(payload, now_ms=now_ms)


class PublicLiquidationCollector:
    def __init__(
        self,
        venue: Venue,
        window: LiquidationWindow,
        *,
        connect: Callable[..., AbstractAsyncContextManager[WebSocketLike]] | None = None,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
        reconnect_seconds: float = 1,
        heartbeat_seconds: float = 20,
        subscription_timeout_seconds: float = 5,
    ) -> None:
        if (
            venue not in (Venue.BINANCE, Venue.BYBIT, Venue.OKX)
            or min(reconnect_seconds, heartbeat_seconds, subscription_timeout_seconds) <= 0
        ):
            raise ValueError("Invalid public collector")
        self.venue, self.window, self.clock = venue, window, clock
        self.connect = connect or cast(
            Callable[..., AbstractAsyncContextManager[WebSocketLike]], websockets.connect
        )
        self.reconnect_seconds = reconnect_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.subscription_timeout_seconds = subscription_timeout_seconds
        self.last_received_at_ms: int | None = None
        self._last_received_monotonic: float | None = None
        self._status = "no_data"
        self.error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def _run(self) -> None:
        urls = {
            Venue.BINANCE: "wss://fstream.binance.com/ws/!forceOrder@arr",
            Venue.BYBIT: "wss://stream.bybit.com/v5/public/linear",
            Venue.OKX: "wss://ws.okx.com/ws/v5/public",
        }
        delay = self.reconnect_seconds
        while not self._stop.is_set():
            try:
                async with self.connect(
                    urls[self.venue],
                    open_timeout=5,
                    close_timeout=2,
                    max_size=1_048_576,
                    max_queue=32,
                    ping_interval=20,
                    ping_timeout=20,
                ) as socket:
                    if self.venue == Venue.BYBIT:
                        await socket.send(
                            json.dumps(
                                {
                                    "op": "subscribe",
                                    "args": [
                                        f"allLiquidation.{s.replace('/', '')}"
                                        for s in SIGNAL_SYMBOLS
                                    ],
                                }
                            )
                        )
                    elif self.venue == Venue.OKX:
                        await socket.send(
                            json.dumps(
                                {
                                    "op": "subscribe",
                                    "args": [{"channel": "liquidation-orders", "instType": "SWAP"}],
                                }
                            )
                        )
                    acknowledged = self.venue == Venue.BINANCE
                    self._status, self.error = (
                        "connected" if acknowledged else "awaiting_subscription",
                        None,
                    )
                    deadline = monotonic() + self.subscription_timeout_seconds
                    iterator = socket.__aiter__()
                    awaiting_pong = False
                    while not self._stop.is_set():
                        timeout = (
                            self.heartbeat_seconds
                            if acknowledged
                            else max(0.001, deadline - monotonic())
                        )
                        try:
                            message = await asyncio.wait_for(anext(iterator), timeout)
                        except TimeoutError:
                            if not acknowledged or awaiting_pong:
                                raise ValueError("Public subscription heartbeat timeout") from None
                            if self.venue == Venue.OKX:
                                await socket.send("ping")
                            elif self.venue == Venue.BYBIT:
                                await socket.send(json.dumps({"op": "ping"}))
                            awaiting_pong = self.venue != Venue.BINANCE
                            continue
                        self.last_received_at_ms = self.clock()
                        self._last_received_monotonic = monotonic()
                        if message in ("pong", b"pong") and self.venue == Venue.OKX:
                            awaiting_pong = False
                            continue
                        try:
                            data = json.loads(message)
                            if isinstance(data, dict):
                                if data.get("event") == "error":
                                    raise RuntimeError("Public subscription rejected")
                                if data.get("op") == "subscribe":
                                    if data.get("success") is not True:
                                        raise RuntimeError("Public subscription rejected")
                                    acknowledged = True
                                    self._status, self.error = "connected", None
                                    continue
                                if data.get("event") == "subscribe":
                                    acknowledged = True
                                    self._status, self.error = "connected", None
                                    continue
                                if data.get("op") in ("pong", "ping"):
                                    if data.get("success", True) is not True:
                                        raise RuntimeError("Public heartbeat rejected")
                                    awaiting_pong = False
                                    continue
                            for event in parse_supported_liquidations(
                                self.venue, data, self.clock()
                            ):
                                self.window.add(event, now_ms=self.clock())
                            acknowledged = True
                            self._status, self.error = "ok", None
                            delay = self.reconnect_seconds
                        except (ValueError, TypeError):
                            self._status, self.error = "error", "invalid_liquidation_evidence"
                self._status, self.error = "error", "public_liquidations_unavailable"
            except Exception:
                self._status, self.error = "error", "public_liquidations_unavailable"
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), delay)
            delay = min(delay * 2, 60)

    @property
    def status(self) -> str:
        if (
            self._status in ("connected", "ok")
            and self._last_received_monotonic is not None
            and monotonic() - self._last_received_monotonic > self.heartbeat_seconds * 2
        ):
            return "stale"
        return self._status

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name=f"liquidations-{self.venue.value}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._status = "stopped"


class WebSocketLike(Protocol):
    async def send(self, message: str) -> None: ...
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...
