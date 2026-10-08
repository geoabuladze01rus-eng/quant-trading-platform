import pytest

from quant_trading_platform.signal_watch.composio_notifications import (
    ComposioEmail,
    ComposioTelegram,
)
from quant_trading_platform.signal_watch.notifications import DeliveryFailure, Event


class Executor:
    def __init__(self):
        self.calls = []
        self.response = {"successful": True, "data": {"ok": True, "result": {"message_id": 123}}}

    async def connection(self, account):
        return {"status": "ACTIVE", "account": account}

    async def schema(self, slug):
        return {"required": ["chat_id", "text"], "properties": {
            "chat_id": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
            "text": {"type": "string"},
        }}

    async def execute(self, slug, account, arguments):
        self.calls.append((slug, account, arguments))
        if slug == "TELEGRAM_GET_ME":
            result = {"id": 42, "username": "Artur01rus_bot", "is_bot": True}
        elif slug == "TELEGRAM_GET_CHAT":
            result = {"id": 8999343417, "type": "private"}
        elif slug == "TELEGRAM_GET_CHAT_MEMBER":
            result = {"status": "member", "user": {"id": 42}}
        else:
            return self.response
        return {"successful": True, "data": {"ok": True, "result": result}}


@pytest.mark.asyncio
async def test_health_checks_permissions_without_sending():
    executor = Executor()
    assert (await ComposioTelegram(executor).health()).healthy
    assert all(slug != "TELEGRAM_SEND_MESSAGE" for slug, _, _ in executor.calls)


@pytest.mark.asyncio
async def test_telegram_success_requires_exact_account_chat_and_receipt():
    executor = Executor()
    receipt = await ComposioTelegram(executor).send(Event("event", "signal", 1, "PAPER ONLY test"))
    assert receipt.message_id == "123"
    assert executor.calls[-1] == (
        "TELEGRAM_SEND_MESSAGE", "ugolovka", {"chat_id": "8999343417", "text": "PAPER ONLY test"}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {"ok": True, "result": {}}, {"ok": False, "result": {"message_id": 123}},
    {"ok": "true", "result": {"message_id": 123}},
    {"ok": True, "result": {"message_id": True}},
])
async def test_missing_or_unverified_message_id_fails(data):
    executor = Executor()
    executor.response = {"successful": True, "data": data}
    with pytest.raises(DeliveryFailure):
        await ComposioTelegram(executor).send(Event("event", "signal", 1, "test"))


def test_other_chat_and_account_rejected():
    with pytest.raises(ValueError):
        ComposioTelegram(Executor(), chat_id="123")
    with pytest.raises(ValueError):
        ComposioTelegram(Executor(), account="other")


@pytest.mark.asyncio
async def test_rate_limit_honors_retry_after():
    executor = Executor()
    executor.response = {"successful": True, "data": {
        "ok": False, "error_code": 429, "parameters": {"retry_after": 7}
    }}
    with pytest.raises(DeliveryFailure) as error:
        await ComposioTelegram(executor).send(Event("event", "signal", 1, "test"))
    assert error.value.retryable and error.value.retry_after == 7


@pytest.mark.asyncio
async def test_email_only_authenticated_recipient_and_confirmed_id():
    class MailExecutor(Executor):
        async def schema(self, slug):
            return {"properties": {"recipient_email": {}, "subject": {}, "body": {}}}

        async def execute(self, slug, account, arguments):
            self.calls.append((slug, account, arguments))
            if slug == "GMAIL_GET_PROFILE":
                return {"successful": True, "data": {"emailAddress": "user@example.com"}}
            return {"successful": True, "data": {"id": "mail-123"}}

    executor = MailExecutor()
    assert (await ComposioEmail(executor).send(Event("e", "s", 1, "test"))).message_id == "mail-123"
    assert executor.calls[-1][2]["recipient_email"] == "user@example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["inactive", "schema", "bot", "chat", "permission", "network"])
async def test_failed_health_never_sends(bad):
    class BadExecutor(Executor):
        async def connection(self, account):
            if bad == "network":
                raise ConnectionError("sensitive")
            return {"status": "INACTIVE" if bad == "inactive" else "ACTIVE", "account": account}

        async def schema(self, slug):
            return {} if bad == "schema" else await super().schema(slug)

        async def execute(self, slug, account, arguments):
            response = await super().execute(slug, account, arguments)
            result = response["data"]["result"]
            if bad == "bot" and slug == "TELEGRAM_GET_ME":
                result["username"] = "other_bot"
            if bad == "chat" and slug == "TELEGRAM_GET_CHAT":
                result["id"] = 123
            if bad == "permission" and slug == "TELEGRAM_GET_CHAT_MEMBER":
                result["status"] = "kicked"
            return response

    executor = BadExecutor()
    assert not (await ComposioTelegram(executor).health()).healthy
    with pytest.raises(DeliveryFailure):
        await ComposioTelegram(executor).send(Event("e", "s", 1, "test"))
    assert all(slug != "TELEGRAM_SEND_MESSAGE" for slug, _, _ in executor.calls)


@pytest.mark.asyncio
async def test_evidence_expiring_during_health_cannot_be_sent():
    executor = Executor()
    sender = ComposioTelegram(executor, clock=lambda: 101)
    with pytest.raises(DeliveryFailure, match="expired_evidence"):
        await sender.send(Event("e", "s", 1, "test", valid_until_ms=100))
    assert all(slug != "TELEGRAM_SEND_MESSAGE" for slug, _, _ in executor.calls)


@pytest.mark.asyncio
async def test_schema_type_drift_fails_closed():
    class Drift(Executor):
        async def schema(self, slug):
            return {"required": ["chat_id", "text"], "properties": {
                "chat_id": {"type": "boolean"}, "text": {"type": "integer"},
            }}

    assert not (await ComposioTelegram(Drift()).health()).healthy
