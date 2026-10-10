from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from time import monotonic_ns

import pytest

from quant_trading_platform.execution_engine import ExecutionEngine, RiskLimits, Signal
from quant_trading_platform.okx_controller import BotManager, ControlError
from quant_trading_platform.signal_watch.journal import Journal
from quant_trading_platform.signal_watch.notifications import CHANNELS, NotificationRouter, Receipt

NOW = 1791500000000
REGISTRY = Path("bot_registry.yaml")


def signal(**changes):
    return replace(
        Signal(
            "s1",
            "BTC/USDT",
            "LONG",
            "HIGH",
            D("78"),
            D("3"),
            "trend",
            D(".01"),
            D(".005"),
            D(".8"),
            D("100"),
            D("10"),
            NOW,
        ),
        **changes,
    )


@pytest.fixture
def context(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        manager = BotManager(journal.connection, REGISTRY, clock=lambda: NOW, sleep=lambda s: None)
        engine = ExecutionEngine(manager, NotificationRouter(journal, {}), clock=lambda: NOW)
        yield journal, manager, engine


def test_signal_start_duplicate_audit_latency(context):
    journal, manager, engine = context
    started = monotonic_ns()
    result = engine.process(signal())
    assert (monotonic_ns() - started) < 1_000_000_000
    assert result.decision == "EXECUTE" and result.bot_id == "BTC_DCA"
    assert result.execution_result == "PAPER_APPLIED"
    assert manager.get_bot_status("BTC_DCA")["status"] == "RUNNING"
    assert engine.process(signal()) == result
    rows = journal.connection.execute("SELECT payload FROM bot_execution_audit").fetchall()
    assert len(rows) == 1 and "78" in rows[0][0] and "paper_only" in rows[0][0]
    assert len(manager.get_positions()) == 1
    assert manager.get_orders() == []


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"confidence": "LOW"}, "confidence"),
        ({"rr": D("1")}, "rr"),
        ({"timestamp_ms": NOW - 1001}, "stale_signal"),
        ({"timestamp_ms": NOW + 1}, "stale_signal"),
        ({"market_state": "unknown"}, "market_state"),
        ({"direction": "SHORT"}, "spot_long_only"),
        ({"risk_usd": D("201"), "notional_usd": D("300")}, "daily_risk"),
        ({"notional_usd": D("1001")}, "max_exposure"),
    ],
)
def test_ignore_fail_closed(context, changes, reason):
    _, manager, engine = context
    result = engine.process(signal(**changes))
    assert result.decision == "IGNORE" and result.reason == reason
    assert not manager.get_positions()


def test_stop_close_cooldown_and_overlap(context):
    _, manager, engine = context
    engine.process(signal())
    assert engine.process(signal(signal_id="s2")).reason == "position_overlap"
    result = engine.process(signal(signal_id="stop", direction="STOP", confidence="LOW"))
    assert result.decision == "STOP"
    assert manager.get_bot_status("BTC_DCA")["status"] == "STOPPED"
    assert manager.get_positions()
    assert engine.process(signal(signal_id="close", direction="CLOSE")).decision == "CLOSE"
    assert not manager.get_positions()
    assert engine.process(signal(signal_id="again")).reason == "cooldown"


def test_pause_resume_and_bad_transitions(context):
    _, manager, _ = context
    manager.start_bot("ETH_GRID", signal_id="one", notional=D("10"))
    assert manager.pause_bot("ETH_GRID", signal_id="pause")["status"] == "PAUSED"
    assert manager.resume_bot("ETH_GRID", signal_id="resume")["status"] == "RUNNING"
    with pytest.raises(ControlError):
        manager.resume_bot("ETH_GRID", signal_id="invalid")
    with pytest.raises(ControlError):
        manager.get_bot_status("unknown")


@pytest.mark.asyncio
async def test_action_notification(context):
    journal, manager, _ = context
    calls = []

    class Channel:
        async def send(self, event):
            calls.append(event)
            return Receipt("123")

    router = NotificationRouter(journal, {name: Channel() for name in CHANNELS})
    engine = ExecutionEngine(manager, router, clock=lambda: NOW)
    result = engine.process(signal())
    assert result.delivery_result == "PENDING"
    await engine.deliver_pending()
    row = engine.result("s1")
    assert row.delivery_result == "SENT"
    assert len(calls) == 3 and all("BTC_DCA" in event.text for event in calls)
    await engine.deliver_pending()
    assert len(calls) == 3


def test_grid_mapping_and_drawdown(context):
    _, manager, engine = context
    result = engine.process(signal(symbol="SOL/USDT", market_state="range", trend_strength=D(".2")))
    assert result.bot_id == "SOL_GRID"
    manager.set_drawdown(D(".11"))
    assert engine.process(signal(signal_id="blocked")).reason == "drawdown"


@pytest.mark.parametrize("value", [D("-1"), D("NaN"), 0.1])
def test_financial_validation_is_strict(value):
    with pytest.raises(ValueError):
        signal(risk_usd=value)


@pytest.mark.parametrize(
    "changes",
    [
        {"signal_id": ""},
        {"symbol": "DOGE/USDT"},
        {"timestamp_ms": 0},
        {"score": D("101")},
        {"direction": "BUY"},
        {"valid_until_ms": -1},
    ],
)
def test_malformed_signal_is_rejected(changes):
    with pytest.raises(ValueError):
        signal(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"cooldown_ms": -1},
        {"max_signal_age_ms": 0},
        {"max_drawdown": D("2")},
        {"daily_risk_usd": D("0")},
    ],
)
def test_risk_limits_validation(changes):
    with pytest.raises(ValueError):
        RiskLimits(**changes)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"direction": "STOP"}, "no_unique_active_bot"),
        ({"confidence": "VERY_HIGH", "score": D("83")}, "confidence"),
        ({"confidence": "HIGH", "score": D("84")}, "confidence"),
        ({"atr_pct": D("0")}, "volatility"),
        ({"volatility": D(".06")}, "volatility"),
        ({"notional_usd": D("0")}, "invalid_size"),
        ({"valid_until_ms": NOW - 1}, "stale_signal"),
    ],
)
def test_additional_decision_gates(context, changes, reason):
    _, _, engine = context
    assert engine.process(signal(**changes)).reason == reason


def test_disabled_registry_and_restart(context):
    journal, manager, engine = context
    journal.connection.execute("UPDATE paper_bots SET status='DISABLED' WHERE bot_id='BTC_DCA'")
    journal.connection.commit()
    assert engine.process(signal()).reason == "bot_state"
    journal.connection.execute("UPDATE paper_bots SET status='STOPPED' WHERE bot_id='BTC_DCA'")
    journal.connection.commit()
    first = engine.process(signal(signal_id="new"))
    restarted = ExecutionEngine(
        BotManager(journal.connection, REGISTRY, clock=lambda: NOW),
        engine.router,
        clock=lambda: NOW,
    )
    assert restarted.process(signal(signal_id="new")) == first
    assert len(restarted.snapshot()) == 2


def test_manager_retry_rollback_and_exhaustion(context, monkeypatch):
    journal, manager, engine = context
    apply = manager._apply
    calls, delays = [], []
    manager.sleep = delays.append

    def transient(operation, bot_id, signal_id, notional):
        calls.append(operation)
        result = apply(operation, bot_id, signal_id, notional)
        if len(calls) < 3:
            raise TimeoutError("upstream secret must not be logged")
        return result

    monkeypatch.setattr(manager, "_apply", transient)
    assert engine.process(signal()).execution_result == "PAPER_APPLIED"
    assert calls == ["start"] * 3 and delays == [1, 2]
    assert len(manager.get_positions()) == 1
    assert journal.connection.execute("SELECT used FROM bot_daily_risk").fetchone()[0] == "10"
    assert (
        journal.connection.execute(
            "SELECT count(*) FROM paper_control_requests " "WHERE operation='start'"
        ).fetchone()[0]
        == 3
    )

    def exhausted(*args):
        raise ConnectionError("secret")

    monkeypatch.setattr(manager, "_apply", exhausted)
    failed = engine.process(signal(signal_id="eth", symbol="ETH/USDT"))
    assert failed.reason == "control_failed" and failed.execution_result == "PAPER_FAILED"
    assert engine.process(signal(signal_id="eth", symbol="ETH/USDT")) == failed
    assert (
        journal.connection.execute("SELECT count(*) FROM paper_bot_allocations").fetchone()[0] == 1
    )


def test_manager_idempotency_and_overlap(context):
    _, manager, _ = context
    first = manager.start_bot("BTC_GRID", signal_id="one", notional=D("10"))
    assert manager.start_bot("BTC_GRID", signal_id="one", notional=D("10")) == first
    with pytest.raises(ControlError, match="idempotency_conflict"):
        manager.start_bot("BTC_GRID", signal_id="one", notional=D("20"))
    manager.stop_bot("BTC_GRID", signal_id="stop")
    with pytest.raises(ControlError, match="position_overlap"):
        manager.start_bot("BTC_GRID", signal_id="other", notional=D("20"))
    with pytest.raises(ControlError, match="signal_id_required"):
        manager.stop_bot("BTC_GRID", signal_id="")
    with pytest.raises(ValueError):
        manager.set_drawdown(D("2"))
    with pytest.raises(ValueError):
        manager.start_bot("ETH_DCA", signal_id="zero", notional=D("0"))


@pytest.mark.parametrize(
    "registry",
    [
        "[]",
        "{}",
        "- bot_id: bad",
        "- bot_id: B\n  symbol: BTC/USDT\n  strategy: GRID\n  status: RUNNING\n"
        '  created_at: "2026-10-09T00:00:00+00:00"\n  last_signal: null',
    ],
)
def test_bad_registry(context, tmp_path, registry):
    journal, _, _ = context
    path = tmp_path / "bad.yaml"
    path.write_text(registry)
    with pytest.raises(ValueError):
        BotManager(journal.connection, path)


def test_shared_database_required(context, tmp_path):
    _, manager, _ = context
    with Journal(tmp_path / "other.sqlite") as other, pytest.raises(ValueError):
        ExecutionEngine(manager, NotificationRouter(other, {}))


def test_candidate_rr_and_missing_target_fail_closed(context):
    from test_signal_intelligence import facts
    from test_signal_watch_engine import frame

    from quant_trading_platform.signal_watch.intelligence import evaluate
    from quant_trading_platform.signal_watch.journal import Candidate

    _, _, engine = context
    result = evaluate(
        [replace(item, timestamp_ms=NOW) for item in facts()],
        now_ms=NOW,
        data_hub_quality="healthy",
    )
    candidate = Candidate("BTC/USDT", "Trend Pullback", result, "trend", NOW)
    candles = frame(
        timestamp_ms=NOW,
        support=D("101"),
        resistance=D("104"),
        low=D("101"),
        high=D("102.5"),
        atr=D(".5"),
    )
    assert (
        engine.consume_candidate(
            candidate, candles, valid_until_ms=NOW + 1000, healthy=True
        ).decision
        == "EXECUTE"
    )
    missing = replace(candidate, timestamp_ms=NOW + 1, asset="ETH/USDT")
    assert (
        engine.consume_candidate(
            missing, replace(candles, resistance=D("101")), valid_until_ms=NOW + 1000, healthy=True
        ).reason
        == "rr"
    )


def test_atomic_audit_failure_rolls_back_control_and_risk(context):
    journal, manager, engine = context
    journal.connection.execute(
        "CREATE TRIGGER fail_audit BEFORE INSERT ON bot_execution_audit "
        "BEGIN SELECT RAISE(ABORT, 'test audit failure'); END"
    )
    journal.connection.commit()
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        engine.process(signal())
    assert manager.get_bot_status("BTC_DCA")["status"] == "STOPPED"
    assert not manager.get_positions()
    assert not journal.connection.execute("SELECT * FROM bot_daily_risk").fetchall()
    assert not journal.connection.execute("SELECT * FROM bot_action_events").fetchall()


def test_registry_file_is_not_rewritten_on_actions(context):
    _, manager, engine = context
    before = REGISTRY.read_bytes()
    engine.process(signal())
    manager.pause_bot("BTC_DCA", signal_id="pause")
    assert REGISTRY.read_bytes() == before
    assert manager.get_bot_status("BTC_DCA")["last_signal"] == "pause"


@pytest.mark.asyncio
async def test_watch_engine_worker_is_independent_and_stop_has_no_tradingview(context):
    import asyncio

    from test_signal_intelligence import facts
    from test_signal_watch_engine import frame

    from quant_trading_platform.signal_watch.engine import WatchEngine
    from quant_trading_platform.signal_watch.intelligence import evaluate
    from quant_trading_platform.signal_watch.journal import Candidate
    from quant_trading_platform.signal_watch.service import WatchService

    journal, manager, _ = context
    gate = asyncio.Event()
    entered = asyncio.Event()

    class Channel:
        async def send(self, event):
            entered.set()
            await gate.wait()
            return Receipt("worker-123")

    class Source:
        closed = False

        def fetch(self, symbol, *, now_ms):
            return frame(
                timestamp_ms=NOW,
                support=D("101"),
                resistance=D("104"),
                low=D("101"),
                high=D("102.5"),
                atr=D(".5"),
            )

        def close(self):
            self.closed = True

    source = Source()
    observations = [replace(item, timestamp_ms=NOW) for item in facts()]
    router = NotificationRouter(journal, {name: Channel() for name in CHANNELS})
    execution = ExecutionEngine(manager, router, clock=lambda: NOW)
    watch = WatchService(
        WatchEngine(journal),
        source,
        lambda symbol, now: {"quality": {"status": "healthy"}},
        external=lambda symbol: observations,
        clock=lambda: NOW,
        notifications=router,
        execution=execution,
    )
    await asyncio.wait_for(watch.poll_once(), timeout=1)
    assert len(manager.get_positions()) == 3
    before = watch.snapshot()
    assert before["bot_control"]
    assert watch.snapshot() == before
    await watch.start()
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert len(manager.get_positions()) == 3
    gate.set()

    async def wait_delivery():
        while any(row["delivery_result"] == "PENDING" for row in execution.snapshot()):
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_delivery(), timeout=2)
    assert all(row["delivery_result"] == "SENT" for row in execution.snapshot())
    await watch.stop()
    assert source.closed
    assessment = evaluate(observations, now_ms=NOW, data_hub_quality="healthy")
    candidate = Candidate("BTC/USDT", "Momentum", assessment, "trend", NOW + 1)
    severe = frame(timestamp_ms=NOW, high=D("108"), low=D("99"), atr=D("2"))
    stopped = execution.consume_candidate(
        candidate, severe, valid_until_ms=NOW + 1000, healthy=True
    )
    assert stopped.decision == "STOP"
    assert manager.get_bot_status("BTC_DCA")["status"] == "STOPPED"
    assert manager.get_positions()  # Stop never silently closes allocations.


def test_router_prohibits_nonatomic_or_live_control(context):
    from quant_trading_platform.okx_controller.manager import atomic

    journal, _, engine = context
    with pytest.raises(ValueError, match="atomic"):
        engine.router.dispatch_paper_control("e", "s", None, lambda: {})
    with pytest.raises(ValueError, match="Live control"), atomic(journal.connection):
        engine.router.dispatch_paper_control(
            "e", "s", None, lambda: {"paper_only": False, "live_execution": True}
        )
    assert not journal.connection.in_transaction
