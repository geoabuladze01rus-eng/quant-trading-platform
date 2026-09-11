from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from time import time

import pytest

from quant_trading_platform.config import Settings
from quant_trading_platform.execution_orchestrator import (
    ExecutionGroupStatus,
    ExecutionHaltedError,
    ExecutionOrchestrator,
)
from quant_trading_platform.market_data.models import NormalizedOrderBook, normalize_order_book
from quant_trading_platform.models import ArbitrageOpportunity, Venue
from quant_trading_platform.paper_trading.coordinator import (
    ExecutionAdmissionError,
    PaperExecutionCoordinator,
)
from quant_trading_platform.persistence.store import SQLitePaperStore


def book(
    venue: Venue,
    now: int,
    *,
    bid: str,
    ask: str,
    bid_qty: str = "10",
    ask_qty: str = "10",
) -> NormalizedOrderBook:
    return normalize_order_book(
        venue,
        "BTC/USDT",
        [[bid, bid_qty]],
        [[ask, ask_qty]],
        now,
        now,
    )


@pytest.fixture
def context(tmp_path):
    now = int(time() * 1000)
    store = SQLitePaperStore(tmp_path / "admission.db")
    store.seed_account(
        balances={"USDT": Decimal("10000"), "BTC": Decimal("10"), "ETH": Decimal(0)}
    )
    orchestrator = ExecutionOrchestrator(store, clock_ms=lambda: now)
    coordinator = PaperExecutionCoordinator(orchestrator, clock_ms=lambda: now)
    buy = book(Venue.BINANCE, now, bid="99", ask="100")
    sell = book(Venue.BYBIT, now, bid="102", ask="103")
    opportunity = ArbitrageOpportunity(
        "cross_venue_spread",
        "BTC/USDT",
        Venue.BINANCE,
        Venue.BYBIT,
        Decimal("2"),
        Decimal("1.75"),
        Decimal("100"),
        now,
        fees_pct=Decimal("0.20"),
        slippage_pct=Decimal("0.05"),
        source_timestamp_ms=now,
    )
    settings = Settings(_env_file=None, max_trade_notional_usd=Decimal("1000"))
    return coordinator, opportunity, buy, sell, settings, now


def test_admission_executes_and_replays_one_durable_group(context) -> None:
    coordinator, opportunity, buy, sell, settings, _ = context
    first = coordinator.execute(
        opportunity,
        buy,
        sell,
        requested_qty=Decimal("1"),
        settings=settings,
        command_id="admission-full",
    )
    second = coordinator.execute(
        opportunity,
        buy,
        sell,
        requested_qty=Decimal("1"),
        settings=settings,
        command_id="admission-full",
    )
    assert first.execution_group.status == ExecutionGroupStatus.COMPLETED
    assert first.reconciliation.ok is True
    assert first.replayed is False and second.replayed is True
    assert second.execution_group.execution_group_id == first.execution_group.execution_group_id
    assert len(second.fills) == 2
    assert len(coordinator.orchestrator.list_groups()) == 1
    assert len(coordinator.orchestrator.store.list_fills()) == 2


def test_same_command_id_cannot_change_execution_intent(context) -> None:
    coordinator, opportunity, buy, sell, settings, _ = context
    coordinator.execute(
        opportunity,
        buy,
        sell,
        requested_qty=Decimal("1"),
        settings=settings,
        command_id="immutable-command",
    )
    with pytest.raises(ValueError, match="different execution intent"):
        coordinator.execute(
            opportunity,
            buy,
            sell,
            requested_qty=Decimal("0.5"),
            settings=settings,
            command_id="immutable-command",
        )


def test_concurrent_command_replay_creates_one_group_and_two_fill_events(context) -> None:
    coordinator, opportunity, buy, sell, settings, _ = context

    def execute():
        return coordinator.execute(
            opportunity,
            buy,
            sell,
            requested_qty=Decimal("1"),
            settings=settings,
            command_id="concurrent-command",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: execute(), range(2)))
    assert len({result.execution_group.execution_group_id for result in results}) == 1
    assert len(coordinator.orchestrator.list_groups()) == 1
    assert len(coordinator.orchestrator.store.list_fills()) == 2
    assert all(
        result.execution_group.status == ExecutionGroupStatus.COMPLETED
        for result in results
    )


def test_partial_primary_legs_remain_hedge_required_without_hedge_book(context) -> None:
    coordinator, opportunity, buy, sell, settings, now = context
    partial_buy = book(
        Venue.BINANCE,
        now,
        bid="99",
        ask="100",
        ask_qty="0.6",
    )
    result = coordinator.execute(
        opportunity,
        partial_buy,
        sell,
        requested_qty=Decimal("1"),
        settings=settings,
        command_id="admission-residual",
    )
    assert result.execution_group.buy_filled_qty == Decimal("0.6")
    assert result.execution_group.sell_filled_qty == Decimal("1")
    assert result.execution_group.residual_qty == Decimal("-0.4")
    assert result.execution_group.status == ExecutionGroupStatus.HEDGE_REQUIRED


def test_bounded_partial_hedges_close_only_evolving_residual(context) -> None:
    coordinator, opportunity, buy, sell, settings, now = context
    partial_buy = book(
        Venue.BINANCE,
        now,
        bid="99",
        ask="100",
        ask_qty="0.6",
    )
    first_hedge = book(
        Venue.OKX,
        now,
        bid="100",
        ask="101",
        ask_qty="0.2",
    )
    second_hedge = book(
        Venue.OKX,
        now,
        bid="100",
        ask="101",
        ask_qty="0.2",
    )
    result = coordinator.execute(
        opportunity,
        partial_buy,
        sell,
        requested_qty=Decimal("1"),
        settings=settings,
        command_id="admission-hedged",
        hedge_books=(first_hedge, second_hedge),
    )
    hedges = [fill for fill in result.fills if fill.is_hedge]
    assert [fill.requested_qty for fill in hedges] == [Decimal("0.4"), Decimal("0.2")]
    assert result.execution_group.residual_qty == 0
    assert result.execution_group.status == ExecutionGroupStatus.COMPLETED
    assert result.reconciliation.ok is True


def test_restart_resumes_existing_group_without_replaying_primary_cash_flow(context) -> None:
    coordinator, opportunity, _, sell, settings, now = context
    partial_buy = book(
        Venue.BINANCE,
        now,
        bid="99",
        ask="100",
        ask_qty="0.6",
    )
    first = coordinator.execute(
        opportunity,
        partial_buy,
        sell,
        requested_qty=Decimal("1"),
        settings=settings,
        command_id="restart-resume",
    )
    balances_before = coordinator.orchestrator.store.list_balances("paper-default")
    restarted = PaperExecutionCoordinator(
        ExecutionOrchestrator(coordinator.orchestrator.store, clock_ms=lambda: now),
        clock_ms=lambda: now,
    )
    hedge = book(
        Venue.OKX,
        now,
        bid="100",
        ask="101",
        ask_qty="0.4",
    )
    resumed = restarted.execute(
        opportunity,
        partial_buy,
        sell,
        requested_qty=Decimal("1"),
        settings=settings,
        command_id="restart-resume",
        hedge_books=(hedge,),
    )
    assert first.execution_group.status == ExecutionGroupStatus.HEDGE_REQUIRED
    assert resumed.replayed is True
    assert resumed.execution_group.execution_group_id == first.execution_group.execution_group_id
    assert resumed.execution_group.status == ExecutionGroupStatus.COMPLETED
    assert len(restarted.orchestrator.list_groups()) == 1
    assert len(restarted.orchestrator.store.list_fills()) == 3
    assert restarted.orchestrator.store.list_balances("paper-default") != balances_before


def test_unavailable_hedge_halts_and_blocks_admission(context) -> None:
    coordinator, opportunity, buy, sell, settings, now = context
    partial_buy = book(
        Venue.BINANCE,
        now,
        bid="99",
        ask="100",
        ask_qty="0.6",
    )
    unavailable = replace(
        book(Venue.OKX, now, bid="100", ask="101"),
        asks=(),
    )
    with pytest.raises(ExecutionHaltedError):
        coordinator.execute(
            opportunity,
            partial_buy,
            sell,
            requested_qty=Decimal("1"),
            settings=settings,
            command_id="admission-halt",
            hedge_books=(unavailable,),
        )
    assert coordinator.orchestrator.halted is True
    with pytest.raises(ExecutionHaltedError, match="recovery/reset"):
        coordinator.execute(
            opportunity,
            buy,
            sell,
            requested_qty=Decimal("1"),
            settings=settings,
            command_id="blocked-after-halt",
        )


@pytest.mark.parametrize("case", ("live", "stale", "future", "balance_mismatch"))
def test_admission_fails_closed_before_group_creation(context, case: str) -> None:
    coordinator, opportunity, buy, sell, settings, now = context
    if case == "live":
        settings = Settings(
            _env_file=None,
            trading_mode="live",
            live_trading_enabled=True,
            max_trade_notional_usd=Decimal("1000"),
        )
    elif case == "stale":
        buy = replace(
            buy,
            timestamp_ms=now - settings.max_market_data_age_ms - 1,
            received_at_ms=now - settings.max_market_data_age_ms - 1,
        )
    elif case == "future":
        buy = replace(buy, timestamp_ms=now + 1, received_at_ms=now + 1)
    else:
        coordinator.orchestrator.store.upsert_balance(
            "paper-default", "BTC", Decimal("9"), Decimal(0)
        )
    with pytest.raises(ExecutionAdmissionError):
        coordinator.execute(
            opportunity,
            buy,
            sell,
            requested_qty=Decimal("1"),
            settings=settings,
            command_id=f"rejected-{case}",
        )
    assert coordinator.orchestrator.list_groups() == ()
    audit = coordinator.orchestrator.store.list_audit(
        "paper-default", correlation_id=f"rejected-{case}"
    )
    assert len(audit) == 1
    assert audit[0]["event"] == "rejected"
