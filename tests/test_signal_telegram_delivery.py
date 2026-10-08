"""Regression contracts migrated from the retired single-channel sender.

The former permissive chat test is replaced by the stronger fixed-recipient contract.
"""

import pytest
from test_signal_intelligence import NOW, facts

from quant_trading_platform.signal_watch.composio_notifications import ComposioTelegram
from quant_trading_platform.signal_watch.intelligence import evaluate
from quant_trading_platform.signal_watch.journal import Candidate, Journal
from quant_trading_platform.signal_watch.notifications import CHANNELS, NotificationRouter, Receipt


def candidate():
    return Candidate(
        "BTC/USDT", "Momentum", evaluate(facts(), now_ms=NOW, data_hub_quality="healthy"),
        "trend", NOW,
    )


class Transport:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def send(self, event):
        self.calls.append(event)
        if self.fail:
            raise TimeoutError("potentially sensitive upstream error")
        return Receipt("message-123")


@pytest.mark.asyncio
async def test_delivery_unbound_by_default_and_durable_idempotent_when_bound(tmp_path):
    path = tmp_path / "journal.sqlite"
    transport = Transport()
    with Journal(path) as journal:
        assert not NotificationRouter(journal, {}).channels
        router = NotificationRouter(journal, {name: transport for name in CHANNELS})
        first = await router.deliver(candidate(), valid_until_ms=NOW + 60_000)
        # Use a deterministic evidence clock for this historical regression candidate.
        assert first.delivery_status == "DATA_BLOCKED"
        router = NotificationRouter(
            journal, {name: transport for name in CHANNELS}, clock=lambda: NOW
        )
        second_candidate = Candidate(
            "ETH/USDT", "Momentum", candidate().result, "trend", NOW
        )
        first = await router.deliver(second_candidate, valid_until_ms=NOW + 60_000)
        assert first.delivery_status == "SENT"
    with Journal(path) as journal:
        second = await NotificationRouter(
            journal, {name: transport for name in CHANNELS}, clock=lambda: NOW
        ).deliver(second_candidate, valid_until_ms=NOW + 60_000)
    assert second == first
    assert len(transport.calls) == 3
    assert "paper" in transport.calls[0].text.lower()


@pytest.mark.asyncio
async def test_uncertain_write_is_not_retried_and_error_is_sanitized(tmp_path):
    transport = Transport()
    transport.fail = True
    with Journal(tmp_path / "journal.sqlite") as journal:
        router = NotificationRouter(journal, {"telegram": transport}, clock=lambda: NOW)
        first = await router.deliver(candidate(), valid_until_ms=NOW + 60_000)
        second = await router.deliver(candidate(), valid_until_ms=NOW + 60_000)
        errors = journal.connection.execute("SELECT error FROM notification_attempts").fetchall()
    assert first.delivery_status == second.delivery_status == "DELIVERY_UNCERTAIN"
    assert any(row[0] == "delivery_outcome_unknown" for row in errors)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("chat", ["first", "second", "", "8999343418"])
def test_same_signal_cannot_be_delivered_to_distinct_chats(chat):
    with pytest.raises(ValueError, match="Unauthorized"):
        ComposioTelegram(None, chat_id=chat)


@pytest.mark.asyncio
async def test_rejected_candidate_never_sent(tmp_path):
    transport = Transport()
    rejected = Candidate(
        "BTC/USDT", "Momentum", evaluate((), now_ms=NOW, data_hub_quality="insufficient"),
        "range", NOW,
    )
    with Journal(tmp_path / "journal.sqlite") as journal:
        result = await NotificationRouter(
            journal, {name: transport for name in CHANNELS}, clock=lambda: NOW
        ).deliver(rejected, valid_until_ms=NOW + 60_000)
    assert result.delivery_status in ("DATA_BLOCKED", "NO_SETUP")
    assert not transport.calls
