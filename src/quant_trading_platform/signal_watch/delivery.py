"""Opt-in Composio Telegram boundary with durable at-most-once attempt semantics.

The caller supplies an authorized Composio transport. No credentials are handled here.
Uncertain writes require external verification, never automatic resend.
"""

import json
from dataclasses import dataclass
from typing import Protocol

from quant_trading_platform.signal_watch.journal import Candidate, Journal


class ComposioTelegramTransport(Protocol):
    async def send_message(self, *, chat_id: str, text: str, signal_id: str) -> str: ...


@dataclass(frozen=True)
class DeliveryResult:
    signal_id: str
    status: str
    message_id: str | None = None
    error: str | None = None


class TelegramDelivery:
    def __init__(
        self,
        journal: Journal,
        transport: ComposioTelegramTransport,
        *,
        chat_id: str,
        enabled: bool = False,
    ) -> None:
        if not chat_id.strip():
            raise ValueError("Delivery requires a resolved chat")
        self.journal, self.transport, self.chat_id = journal, transport, chat_id
        self.enabled = enabled
        with journal.connection:
            journal.connection.execute(
                "CREATE TABLE IF NOT EXISTS signal_deliveries "
                "(signal_id TEXT NOT NULL, chat_id TEXT NOT NULL, status TEXT NOT NULL, "
                "message_id TEXT, error TEXT, PRIMARY KEY(signal_id, chat_id))"
            )

    async def deliver(self, candidate: Candidate) -> DeliveryResult:
        signal_id = self.journal.record(candidate)
        if candidate.result.rejected_reason is not None:
            return DeliveryResult(signal_id, "rejected")
        if not self.enabled:
            return DeliveryResult(signal_id, "disabled")
        connection = self.journal.connection
        with connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO signal_deliveries VALUES (?, ?, ?, NULL, NULL)",
                (signal_id, self.chat_id, "pending"),
            ).rowcount
        if not inserted:
            row = connection.execute(
                "SELECT status, message_id, error FROM signal_deliveries "
                "WHERE signal_id=? AND chat_id=?",
                (signal_id, self.chat_id),
            ).fetchone()
            return DeliveryResult(signal_id, row[0], row[1], row[2])
        text = "PAPER ONLY · Crypto Signal Watch v4\n" + json.dumps(
            candidate.payload(), ensure_ascii=False
        )
        try:
            message_id = await self.transport.send_message(
                chat_id=self.chat_id, text=text, signal_id=signal_id
            )
            if not isinstance(message_id, str) or not message_id.strip():
                raise ValueError("Unverified message identity")
            result = DeliveryResult(signal_id, "delivered", message_id)
        except Exception:
            result = DeliveryResult(signal_id, "unknown", error="delivery_outcome_unknown")
        with connection:
            connection.execute(
                "UPDATE signal_deliveries SET status=?, message_id=?, error=? "
                "WHERE signal_id=? AND chat_id=?",
                (result.status, result.message_id, result.error, signal_id, self.chat_id),
            )
        return result
