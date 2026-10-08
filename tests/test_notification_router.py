import asyncio

import pytest

from quant_trading_platform.signal_watch.journal import Journal
from quant_trading_platform.signal_watch.notifications import (
    CHANNELS,
    DeliveryFailure,
    NotificationRouter,
    Receipt,
)


class Sender:
    def __init__(self, results=None):
        self.results = list(results or [Receipt("123")])
        self.calls = []

    async def send(self, event):
        self.calls.append(event.signal_id)
        result = self.results[min(len(self.calls) - 1, len(self.results) - 1)]
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.mark.asyncio
async def test_router_success_order_and_durable_duplicate(tmp_path):
    order = []

    class Ordered(Sender):
        def __init__(self, name):
            super().__init__()
            self.name = name

        async def send(self, event):
            order.append(self.name)
            return await super().send(event)

    senders = {name: Ordered(name) for name in CHANNELS}
    path = tmp_path / "journal.sqlite"
    with Journal(path) as journal:
        router = NotificationRouter(journal, senders)
        first = await router.route("signal-1", "PAPER ONLY test")
        second = await router.route("signal-1", "PAPER ONLY test")
        assert first == second
        assert first.delivery_status == "SENT"
        assert first.event_id and first.created_at > 0
        assert order == list(CHANNELS)
    with Journal(path) as journal:
        assert (await NotificationRouter(journal, senders).route("signal-1", "test")) == first
    assert all(len(sender.calls) == 1 for sender in senders.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [429, 500, "timeout", "network"])
async def test_safe_retry_is_bounded(tmp_path, failure):
    delays = []

    async def wait(seconds):
        delays.append(seconds)

    telegram = Sender([DeliveryFailure(str(failure), retryable=True), Receipt("456")])
    with Journal(tmp_path / "journal.sqlite") as journal:
        router = NotificationRouter(
            journal, {**{name: Sender() for name in CHANNELS}, "telegram": telegram}, sleep=wait
        )
        result = await router.route("signal", "test")
    assert result.delivery_status == "SENT"
    assert result.retry_count == 1
    assert len(telegram.calls) == 2
    assert delays == [1]


@pytest.mark.asyncio
async def test_exhausted_retries_never_restart(tmp_path):
    telegram = Sender([DeliveryFailure("429", retryable=True)])
    with Journal(tmp_path / "journal.sqlite") as journal:
        router = NotificationRouter(
            journal, {**{name: Sender() for name in CHANNELS}, "telegram": telegram},
            sleep=lambda seconds: asyncio.sleep(0),
        )
        first = await router.route("signal", "test")
        assert await router.route("signal", "test") == first
    assert first.delivery_status == "DELIVERY_FAILED"
    assert first.retry_count == 2
    assert len(telegram.calls) == 3


@pytest.mark.asyncio
async def test_post_dispatch_timeout_is_uncertain_and_not_repeated(tmp_path):
    telegram = Sender([TimeoutError("secret upstream details")])
    with Journal(tmp_path / "journal.sqlite") as journal:
        router = NotificationRouter(
            journal, {**{name: Sender() for name in CHANNELS}, "telegram": telegram}
        )
        first = await router.route("signal", "test")
        assert await router.route("signal", "test") == first
        assert first.delivery_status == "DELIVERY_UNCERTAIN"
    assert len(telegram.calls) == 1


@pytest.mark.asyncio
async def test_missing_channel_cannot_be_sent(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        result = await NotificationRouter(journal, {}).route("signal", "test")
    assert result.delivery_status == "DELIVERY_FAILED"


@pytest.mark.asyncio
async def test_concurrent_duplicate_is_reserved_before_send(tmp_path):
    gate = asyncio.Event()

    class Slow(Sender):
        async def send(self, event):
            await gate.wait()
            return await super().send(event)

    senders = {name: Slow() for name in CHANNELS}
    with Journal(tmp_path / "journal.sqlite") as journal:
        router = NotificationRouter(journal, senders)
        task = asyncio.create_task(router.route("signal", "test"))
        await asyncio.sleep(0)
        assert (await router.route("signal", "test")).delivery_status == "PENDING"
        gate.set()
        assert (await task).delivery_status == "SENT"
    assert all(len(sender.calls) == 1 for sender in senders.values())


@pytest.mark.asyncio
async def test_restart_pending_is_uncertain_not_replayed(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        router = NotificationRouter(journal, {})
        journal.connection.execute(
            "INSERT INTO notification_events VALUES ('crashed', 'event', 42, 'PENDING', 0)"
        )
        journal.connection.commit()
        result = await router.route("crashed", "test")
        assert result.delivery_status == "DELIVERY_UNCERTAIN"
        assert result.created_at == 42


@pytest.mark.asyncio
async def test_expired_evidence_is_not_dispatched(tmp_path):
    senders = {name: Sender() for name in CHANNELS}
    with Journal(tmp_path / "journal.sqlite") as journal:
        router = NotificationRouter(journal, senders, clock=lambda: 100)
        assert (await router.route("expired", "test", valid_until_ms=99)).delivery_status == (
            "DATA_BLOCKED"
        )
    assert all(not sender.calls for sender in senders.values())


@pytest.mark.asyncio
async def test_legacy_sent_or_uncertain_never_replayed(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        journal.connection.execute(
            "CREATE TABLE signal_deliveries (signal_id TEXT, status TEXT)"
        )
        journal.connection.execute("INSERT INTO signal_deliveries VALUES ('old', 'delivered')")
        journal.connection.commit()
        router = NotificationRouter(journal, {name: Sender() for name in CHANNELS})
        assert (await router.route("old", "test")).delivery_status == "DELIVERY_UNCERTAIN"


@pytest.mark.asyncio
async def test_audit_has_receipt_chat_latency_and_sanitized_error(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        router = NotificationRouter(journal, {name: Sender() for name in CHANNELS})
        await router.route("signal", "test")
        rows = journal.connection.execute(
            "SELECT signal_id, message_id, chat_id, delivery_status, error, retry_count, "
            "latency, timestamp FROM notification_attempts WHERE channel='telegram'"
        ).fetchall()
        assert rows[-1][:6] == ("signal", "123", "8999343417", "SENT", None, 0)
        assert rows[-1][6] >= 0 and rows[-1][7] > 0
        assert router.snapshot()[0]["delivery_status"] == "SENT"


@pytest.mark.asyncio
async def test_watch_routes_confirmed_candidates_and_snapshot_is_read_only(tmp_path):
    from test_signal_intelligence import NOW, facts
    from test_signal_watch_engine import frame

    from quant_trading_platform.signal_watch.engine import WatchEngine
    from quant_trading_platform.signal_watch.service import WatchService

    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame(timestamp_ms=now_ms)

        def close(self):
            pass

    senders = {name: Sender() for name in CHANNELS}
    with Journal(tmp_path / "journal.sqlite") as journal:
        router = NotificationRouter(journal, senders, clock=lambda: NOW)
        watch = WatchService(
            WatchEngine(journal), Source(), lambda symbol, now: {"quality": {"status": "healthy"}},
            external=lambda symbol: facts(), clock=lambda: NOW, notifications=router,
        )
        await watch.poll_once()
        state = watch.snapshot()
        assert state["notifications"]
        accepted = {row["signal_id"] for row in watch.engine.recent_diagnostics()
                    if row["confidence"] in ("HIGH", "VERY HIGH")}
        assert accepted
        assert all(event["delivery_status"] == (
            "SENT" if event["signal_id"] in accepted else "NO_SETUP"
        ) for event in state["notifications"])
        counts = [len(sender.calls) for sender in senders.values()]
        assert counts[0] > 0 and len(set(counts)) == 1
        assert watch.snapshot() == state
        await watch.poll_once()
        assert [len(sender.calls) for sender in senders.values()] == counts

        blocked = WatchService(
            WatchEngine(journal), Source(),
            lambda symbol, now: {"quality": {"status": "insufficient"}},
            external=lambda symbol: facts(), clock=lambda: NOW, notifications=router,
        )
        await blocked.poll_once()
        blocked_ids = {row["signal_id"] for asset in blocked.snapshot()["assets"].values()
                       for row in asset["candidates"]}
        assert blocked_ids
        assert all(event["delivery_status"] == "DATA_BLOCKED"
                   for event in blocked.snapshot()["notifications"]
                   if event["signal_id"] in blocked_ids)
        assert [len(sender.calls) for sender in senders.values()] == counts


@pytest.mark.asyncio
async def test_message_is_bounded_even_when_full_evidence_is_large(tmp_path):
    from dataclasses import replace

    from test_signal_intelligence import NOW, facts

    from quant_trading_platform.signal_watch.intelligence import evaluate
    from quant_trading_platform.signal_watch.journal import Candidate

    events = []

    class Capture(Sender):
        async def send(self, event):
            events.append(event)
            return Receipt("123")

    evidence = [replace(item, origin=item.origin * 5000) for item in facts()]
    candidate = Candidate(
        "BTC/USDT", "Momentum", evaluate(evidence, now_ms=NOW, data_hub_quality="healthy"),
        "trend", NOW,
    )
    with Journal(tmp_path / "journal.sqlite") as journal:
        result = await NotificationRouter(
            journal, {name: Capture() for name in CHANNELS}, clock=lambda: NOW
        ).deliver(candidate, valid_until_ms=NOW + 60_000)
    assert result.delivery_status == "SENT"
    assert all(len(event.text) <= 4096 for event in events)
    assert all(candidate.signal_id in event.text for event in events)
