import pytest
from test_signal_intelligence import NOW, facts

from quant_trading_platform.signal_watch.delivery import TelegramDelivery
from quant_trading_platform.signal_watch.intelligence import evaluate
from quant_trading_platform.signal_watch.journal import Candidate, Journal


def candidate():
    return Candidate(
        "BTC/USDT",
        "Momentum",
        evaluate(facts(), now_ms=NOW, data_hub_quality="healthy"),
        "trend",
        NOW,
    )


class Transport:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def send_message(self, *, chat_id, text, signal_id):
        self.calls.append((chat_id, text, signal_id))
        if self.fail:
            raise TimeoutError("potentially sensitive upstream error")
        return "message-123"


@pytest.mark.asyncio
async def test_delivery_is_disabled_by_default_and_durable_idempotent_when_enabled(tmp_path):
    path = tmp_path / "journal.sqlite"
    transport = Transport()
    with Journal(path) as journal:
        disabled = TelegramDelivery(journal, transport, chat_id="private-chat")
        assert (await disabled.deliver(candidate())).status == "disabled"
        assert not transport.calls
        sender = TelegramDelivery(journal, transport, chat_id="private-chat", enabled=True)
        first = await sender.deliver(candidate())
        assert first.status == "delivered"
        assert first.message_id == "message-123"
        assert first.signal_id == candidate().signal_id
    with Journal(path) as journal:
        second = await TelegramDelivery(
            journal, transport, chat_id="private-chat", enabled=True
        ).deliver(candidate())
    assert second == first
    assert len(transport.calls) == 1
    assert "paper" in transport.calls[0][1].lower()


@pytest.mark.asyncio
async def test_uncertain_write_is_not_retried_and_error_is_sanitized(tmp_path):
    transport = Transport()
    transport.fail = True
    with Journal(tmp_path / "journal.sqlite") as journal:
        sender = TelegramDelivery(journal, transport, chat_id="chat", enabled=True)
        first = await sender.deliver(candidate())
        second = await sender.deliver(candidate())
    assert first.status == second.status == "unknown"
    assert first.error == "delivery_outcome_unknown"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_same_signal_can_be_delivered_to_distinct_chats(tmp_path):
    transport = Transport()
    with Journal(tmp_path / "journal.sqlite") as journal:
        for chat in ("first", "second"):
            assert (
                await TelegramDelivery(journal, transport, chat_id=chat, enabled=True).deliver(
                    candidate()
                )
            ).status == "delivered"
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_rejected_candidate_never_sent(tmp_path):
    transport = Transport()
    rejected = Candidate(
        "BTC/USDT",
        "Momentum",
        evaluate((), now_ms=NOW, data_hub_quality="insufficient"),
        "range",
        NOW,
    )
    with Journal(tmp_path / "journal.sqlite") as journal:
        assert (
            await TelegramDelivery(journal, transport, chat_id="chat", enabled=True).deliver(
                rejected
            )
        ).status == "rejected"
    assert not transport.calls
