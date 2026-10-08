"""Durable, ordered paper notification delivery; ambiguous writes never replay."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, replace
from time import monotonic_ns, time_ns
from typing import Literal, Protocol, cast

from quant_trading_platform.signal_watch.journal import Candidate, Journal

CHANNELS = ("chatgpt_push", "email", "telegram")
Status = Literal[
    "PENDING", "SENT", "DELIVERY_FAILED", "DELIVERY_UNCERTAIN", "SKIPPED",
    "NO_SETUP", "DATA_BLOCKED",
]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    event_id: str
    signal_id: str
    created_at: int
    text: str
    delivery_status: Status = "PENDING"
    retry_count: int = 0
    valid_until_ms: int | None = None


@dataclass(frozen=True)
class Receipt:
    message_id: str


@dataclass(frozen=True)
class DeliveryResult:
    event_id: str
    signal_id: str
    created_at: int
    delivery_status: Status
    retry_count: int


class DeliveryFailure(Exception):
    """An explicitly rejected or provably undispatched request, safe to retry."""

    def __init__(self, code: str, *, retryable: bool = False, retry_after: int = 0):
        super().__init__(code)
        self.code, self.retryable, self.retry_after = code, retryable, retry_after


class Channel(Protocol):
    async def send(self, event: Event) -> Receipt: ...


class NotificationRouter:
    def __init__(
        self, journal: Journal, channels: Mapping[str, Channel], *,
        sleep: Callable[[int], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
        channel_timeout_seconds: int = 20,
    ) -> None:
        if set(channels) - set(CHANNELS):
            raise ValueError("Unsupported notification channel")
        if type(channel_timeout_seconds) is not int or channel_timeout_seconds <= 0:
            raise ValueError("Invalid delivery timeout")
        self.channel_timeout_seconds = channel_timeout_seconds
        self.journal, self.channels, self.sleep, self.clock = journal, dict(channels), sleep, clock
        self.active: set[str] = set()
        with journal.connection:
            journal.connection.execute(
                "CREATE TABLE IF NOT EXISTS notification_events ("
                "signal_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, created_at INTEGER NOT NULL, "
                "delivery_status TEXT NOT NULL, retry_count INTEGER NOT NULL)"
            )
            journal.connection.execute(
                "CREATE TABLE IF NOT EXISTS notification_attempts ("
                "signal_id TEXT NOT NULL, channel TEXT NOT NULL, message_id TEXT, chat_id TEXT, "
                "delivery_status TEXT NOT NULL, error TEXT, retry_count INTEGER NOT NULL, "
                "latency INTEGER NOT NULL, timestamp INTEGER NOT NULL)"
            )

    def result(self, signal_id: str) -> DeliveryResult | None:
        row = self.journal.connection.execute(
            "SELECT event_id, signal_id, created_at, delivery_status, retry_count "
            "FROM notification_events WHERE signal_id=?", (signal_id,),
        ).fetchone()
        return DeliveryResult(*row) if row else None

    def _finish(self, event: Event, status: Status, retries: int) -> DeliveryResult:
        with self.journal.connection:
            self.journal.connection.execute(
                "UPDATE notification_events SET delivery_status=?, retry_count=? WHERE signal_id=?",
                (status, retries, event.signal_id),
            )
        return DeliveryResult(event.event_id, event.signal_id, event.created_at, status, retries)

    def _log(
        self, event: Event, channel: str, status: Status, retry_count: int, latency: int,
        *, message_id: str | None = None, error: str | None = None,
    ) -> None:
        chat_id = "8999343417" if channel == "telegram" else None
        values = (event.signal_id, channel, message_id, chat_id, status, error,
                  retry_count, latency, self.clock())
        with self.journal.connection:
            self.journal.connection.execute(
                "INSERT INTO notification_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", values
            )
        LOGGER.info("notification_delivery", extra=dict(zip(
            ("signal_id", "channel", "message_id", "chat_id", "delivery_status", "error",
             "retry_count", "latency", "timestamp"), values, strict=True,
        )))

    async def route(
        self, signal_id: str, text: str, *, disposition: Status = "PENDING",
        valid_until_ms: int | None = None,
    ) -> DeliveryResult:
        if not signal_id.strip() or not text.strip() or disposition not in (
            "PENDING", "SKIPPED", "NO_SETUP", "DATA_BLOCKED"
        ):
            raise ValueError("Invalid notification event")
        event = Event(hashlib.sha256(("notification:" + signal_id).encode()).hexdigest(),
                      signal_id, self.clock(), text, valid_until_ms=valid_until_ms)
        with self.journal.connection:
            inserted = self.journal.connection.execute(
                "INSERT OR IGNORE INTO notification_events VALUES (?, ?, ?, ?, 0)",
                (signal_id, event.event_id, event.created_at, disposition),
            ).rowcount
        if not inserted:
            existing = self.result(signal_id)
            assert existing is not None
            if existing.delivery_status == "PENDING" and signal_id not in self.active:
                # A prior process may have dispatched before crashing. Never replay it.
                recovered = Event(existing.event_id, signal_id, existing.created_at, text)
                return self._finish(recovered, "DELIVERY_UNCERTAIN", existing.retry_count)
            return existing
        if disposition != "PENDING":
            return self._finish(event, disposition, 0)
        legacy = self.journal.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_deliveries'"
        ).fetchone()
        if legacy and self.journal.connection.execute(
            "SELECT 1 FROM signal_deliveries WHERE signal_id=?", (signal_id,)
        ).fetchone():
            return self._finish(event, "DELIVERY_UNCERTAIN", 0)
        self.active.add(signal_id)
        states: list[Status] = []
        retries = 0
        try:
            for name in CHANNELS:
                sender = self.channels.get(name)
                if sender is None:
                    states.append("DELIVERY_FAILED")
                    self._log(event, name, "DELIVERY_FAILED", 0, 0, error="channel_unbound")
                    continue
                for attempt in range(3):
                    if valid_until_ms is not None and self.clock() > valid_until_ms:
                        states.append("DATA_BLOCKED")
                        self._log(event, name, "DATA_BLOCKED", attempt, 0, error="expired_evidence")
                        break
                    started = monotonic_ns()
                    # Persist the reservation before the external side effect.
                    self._log(event, name, "PENDING", attempt, 0)
                    message_id, error = None, None
                    status: Status = "DELIVERY_FAILED"
                    retry_delay = None
                    try:
                        receipt = await asyncio.wait_for(
                            sender.send(replace(event, retry_count=attempt)),
                            timeout=self.channel_timeout_seconds,
                        )
                        if (not isinstance(receipt, Receipt)
                                or not isinstance(receipt.message_id, str)
                                or not receipt.message_id.strip()):
                            error = "missing_message_id"
                        else:
                            message_id, status = receipt.message_id, "SENT"
                    except DeliveryFailure as exc:
                        error = exc.code
                        if error == "expired_evidence":
                            status = "DATA_BLOCKED"
                        if exc.retryable and attempt < 2:
                            retry_delay = max(2**attempt, exc.retry_after)
                    except Exception:
                        status, error = "DELIVERY_UNCERTAIN", "delivery_outcome_unknown"
                    self._log(event, name, status, attempt, (monotonic_ns() - started) // 1_000_000,
                              message_id=message_id, error=error)
                    if retry_delay is None:
                        states.append(status)
                        break
                    retries += 1
                    await self.sleep(retry_delay)
            aggregate: Status = "SENT"
            if "DELIVERY_UNCERTAIN" in states:
                aggregate = "DELIVERY_UNCERTAIN"
            elif "DELIVERY_FAILED" in states:
                aggregate = "DELIVERY_FAILED"
            elif "DATA_BLOCKED" in states:
                aggregate = "DATA_BLOCKED"
            return self._finish(event, aggregate, retries)
        finally:
            self.active.discard(signal_id)

    async def deliver(
        self, candidate: Candidate, *, valid_until_ms: int, data_blocked: bool = False,
    ) -> DeliveryResult:
        self.journal.record(candidate)
        disposition: Status = "PENDING"
        if data_blocked or candidate.result.rejected_reason == "stale_structure":
            disposition = "DATA_BLOCKED"
        elif candidate.result.rejected_reason:
            disposition = (
                "DATA_BLOCKED" if "data" in candidate.result.rejected_reason else "NO_SETUP"
            )
        elif candidate.result.confidence not in ("HIGH", "VERY HIGH"):
            disposition = "NO_SETUP"
        # Keep full provenance in the journal, not in an oversized Telegram message.
        payload = candidate.payload()
        summary = {key: payload[key] for key in (
            "asset", "setup", "score", "domains", "confidence", "market_regime",
            "timestamp_ms", "paper_only", "live_execution",
        )}
        summary["signal_id"] = candidate.signal_id
        return await self.route(
            candidate.signal_id, "PAPER ONLY · Crypto Signal Watch v4\n" + json.dumps(
                summary, ensure_ascii=False
            ), disposition=disposition, valid_until_ms=valid_until_ms,
        )

    def snapshot(self, limit: int = 100) -> list[dict[str, object]]:
        rows = self.journal.connection.execute(
            "SELECT event_id, signal_id, created_at, delivery_status, retry_count "
            "FROM notification_events ORDER BY rowid DESC LIMIT ?", (limit,),
        ).fetchall()
        return [cast(dict[str, object], asdict(DeliveryResult(*row))) for row in rows]
