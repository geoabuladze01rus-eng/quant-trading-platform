"""Native Composio boundary: no bot tokens, HTTP fallback, or arbitrary recipients."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import time_ns
from typing import Protocol

from quant_trading_platform.signal_watch.notifications import DeliveryFailure, Event, Receipt


class ComposioExecutor(Protocol):
    async def connection(self, account: str) -> Mapping[str, object]: ...
    async def schema(self, slug: str) -> Mapping[str, object]: ...
    async def execute(
        self, slug: str, account: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class TelegramHealth:
    healthy: bool
    connection: bool
    schema: bool
    chat: bool
    send_permission: bool
    error: str | None = None


def _data(response: Mapping[str, object]) -> Mapping[str, object]:
    data = response.get("data")
    return data if response.get("successful") is True and isinstance(data, Mapping) else {}


def _result(response: Mapping[str, object]) -> Mapping[str, object]:
    data = _data(response)
    result = data.get("result")
    return result if data.get("ok") is True and isinstance(result, Mapping) else {}


class ComposioTelegram:
    def __init__(
        self, executor: ComposioExecutor, *, account: str = "ugolovka",
        chat_id: str = "8999343417",
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
    ) -> None:
        if account != "ugolovka" or chat_id != "8999343417":
            raise ValueError("Unauthorized Telegram account or chat")
        self.executor, self.account, self.chat_id = executor, account, chat_id
        self.clock = clock

    async def health(self) -> TelegramHealth:
        connection_ok = schema_ok = chat_ok = permission_ok = False
        try:
            connection = await self.executor.connection(self.account)
            connection_ok = (
                connection.get("status") == "ACTIVE" and connection.get("account") == self.account
            )
            schema = await self.executor.schema("TELEGRAM_SEND_MESSAGE")
            required, properties = schema.get("required"), schema.get("properties")
            text_spec = properties.get("text") if isinstance(properties, Mapping) else None
            chat_spec = properties.get("chat_id") if isinstance(properties, Mapping) else None
            variants = chat_spec.get("anyOf", []) if isinstance(chat_spec, Mapping) else []
            chat_type_ok = isinstance(chat_spec, Mapping) and (
                chat_spec.get("type") in ("integer", "string")
                or (isinstance(variants, list) and any(
                    isinstance(item, Mapping) and item.get("type") in ("integer", "string")
                    for item in variants
                ))
            )
            schema_ok = (
                isinstance(required, list) and {"chat_id", "text"} <= set(required)
                and isinstance(properties, Mapping) and {"chat_id", "text"} <= set(properties)
                and isinstance(text_spec, Mapping) and text_spec.get("type") == "string"
                and chat_type_ok
            )
            if not connection_ok or not schema_ok:
                return TelegramHealth(False, connection_ok, schema_ok, False, False,
                                      "connection_or_schema_unverified")
            bot = _result(await self.executor.execute("TELEGRAM_GET_ME", self.account, {}))
            bot_id = bot.get("id")
            if (type(bot_id) is not int or bot.get("is_bot") is not True
                    or bot.get("username") != "Artur01rus_bot"):
                return TelegramHealth(False, True, True, False, False, "bot_mismatch")
            chat = _result(await self.executor.execute(
                "TELEGRAM_GET_CHAT", self.account, {"chat_id": self.chat_id}
            ))
            chat_ok = type(chat.get("id")) is int and str(chat.get("id")) == self.chat_id
            if not chat_ok or chat.get("type") != "private":
                return TelegramHealth(False, True, True, False, False, "chat_mismatch")
            member = _result(await self.executor.execute(
                "TELEGRAM_GET_CHAT_MEMBER", self.account,
                {"chat_id": self.chat_id, "user_id": bot_id},
            ))
            user = member.get("user")
            permission_ok = (
                member.get("status") in ("member", "administrator", "creator")
                and isinstance(user, Mapping) and user.get("id") == bot_id
            )
            return TelegramHealth(permission_ok, True, True, True, permission_ok,
                                  None if permission_ok else "send_permission_unverified")
        except Exception:
            return TelegramHealth(False, connection_ok, schema_ok, chat_ok, permission_ok,
                                  "health_unavailable")

    async def send(self, event: Event) -> Receipt:
        health = await self.health()
        if not health.healthy:
            raise DeliveryFailure(health.error or "health_unavailable",
                                  retryable=health.error == "health_unavailable")
        if not 1 <= len(event.text) <= 4096:
            raise DeliveryFailure("invalid_message_length")
        if event.valid_until_ms is not None and self.clock() > event.valid_until_ms:
            raise DeliveryFailure("expired_evidence")
        response = await self.executor.execute(
            "TELEGRAM_SEND_MESSAGE", self.account, {"chat_id": self.chat_id, "text": event.text}
        )
        data = _data(response)
        result = _result(response)
        message_id = result.get("message_id")
        if type(message_id) is int and message_id > 0:
            chat = result.get("chat")
            if isinstance(chat, Mapping) and str(chat.get("id")) != self.chat_id:
                raise DeliveryFailure("receipt_chat_mismatch")
            return Receipt(str(message_id))
        code = data.get("error_code")
        if data.get("ok") is False and type(code) is int and code in (429, 500):
            parameters = data.get("parameters")
            delay = parameters.get("retry_after") if isinstance(parameters, Mapping) else 0
            raise DeliveryFailure(str(code), retryable=True,
                                  retry_after=delay if type(delay) is int and delay >= 0 else 0)
        # Missing message_id is terminal: no speculative resend of a possibly accepted message.
        raise DeliveryFailure("missing_or_unverified_message_id")


class ComposioEmail:
    """Send only to the authenticated mailbox through its authorized Composio account."""

    def __init__(
        self, executor: ComposioExecutor, *, account: str = "work-gmail",
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
    ) -> None:
        self.executor, self.account = executor, account
        self.clock = clock

    async def send(self, event: Event) -> Receipt:
        try:
            connection = await self.executor.connection(self.account)
            schema = await self.executor.schema("GMAIL_SEND_EMAIL")
            properties = schema.get("properties")
            if (connection.get("status") != "ACTIVE"
                    or connection.get("account") != self.account
                    or not isinstance(properties, Mapping)
                    or not {"recipient_email", "body", "subject"} <= set(properties)):
                raise DeliveryFailure("email_preflight_failed")
            profile = _data(await self.executor.execute(
                "GMAIL_GET_PROFILE", self.account, {"user_id": "me"}
            ))
            address = profile.get("emailAddress")
            if not isinstance(address, str) or "@" not in address or "\n" in address:
                raise DeliveryFailure("email_recipient_unverified")
        except DeliveryFailure:
            raise
        except Exception:
            raise DeliveryFailure("email_preflight_unavailable", retryable=True) from None
        if event.valid_until_ms is not None and self.clock() > event.valid_until_ms:
            raise DeliveryFailure("expired_evidence")
        response = await self.executor.execute("GMAIL_SEND_EMAIL", self.account, {
            "recipient_email": address, "subject": "Crypto Signal Watch v4 · PAPER ONLY",
            "body": event.text, "is_html": False, "user_id": "me",
        })
        message_id = _data(response).get("id")
        if not isinstance(message_id, str) or not message_id.strip():
            raise DeliveryFailure("email_message_id_unverified")
        return Receipt(message_id)
