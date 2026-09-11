from dataclasses import replace
from decimal import Decimal

import pytest

from quant_trading_platform.execution_orchestrator import (
    ExecutionGroupStatus,
    ExecutionHaltedError,
    ExecutionOrchestrator,
)
from quant_trading_platform.market_data.models import NormalizedOrderBook, normalize_order_book
from quant_trading_platform.models import Venue
from quant_trading_platform.paper_trading.service import PersistentPaperService
from quant_trading_platform.persistence.store import SQLitePaperStore
from quant_trading_platform.risk import RiskDecision

NOW = 10_000
APPROVED = RiskDecision(True, "Approved for paper lifecycle", ("paper_only",))


def book(venue: Venue, *, bid_qty: str = "100", ask_qty: str = "100") -> NormalizedOrderBook:
    bid, ask = (("9", "10") if venue == Venue.BINANCE else ("11", "12"))
    return normalize_order_book(
        venue,
        "BTC/USDT",
        [[bid, bid_qty]],
        [[ask, ask_qty]],
        NOW,
        NOW,
    )


@pytest.fixture
def runner(tmp_path) -> ExecutionOrchestrator:
    store = SQLitePaperStore(tmp_path / "lifecycle.db")
    store.seed_account(
        balances={"USDT": Decimal("10000"), "BTC": Decimal("1000"), "ETH": Decimal(0)}
    )
    return ExecutionOrchestrator(store, clock_ms=lambda: NOW)


def group(runner: ExecutionOrchestrator):
    created = runner.create_group(
        "BTC/USDT",
        Venue.BINANCE,
        Venue.BYBIT,
        Decimal("100"),
        risk_decision=APPROVED,
    )
    return runner.reserve_group(created.execution_group_id, buy_expected_price=Decimal("10"))


def test_group_creation_requires_approved_canonical_risk_decision(
    runner: ExecutionOrchestrator,
) -> None:
    rejected = RiskDecision(False, "Balance mismatch: trading stopped")
    with pytest.raises(ValueError, match="Approved RiskDecision"):
        runner.create_group(
            "BTC/USDT",
            Venue.BINANCE,
            Venue.BYBIT,
            Decimal("100"),
            risk_decision=rejected,
        )
    assert runner.store.list_orders() == []


def test_both_legs_are_reserved_atomically_before_any_fill(
    runner: ExecutionOrchestrator,
) -> None:
    created = runner.create_group(
        "BTC/USDT",
        Venue.BINANCE,
        Venue.BYBIT,
        Decimal("100"),
        risk_decision=APPROVED,
    )
    with pytest.raises(ValueError, match="must be reserved"):
        runner.submit_fill(
            created.execution_group_id,
            "buy",
            book(Venue.BINANCE),
            Decimal("10"),
            "unreserved-fill",
        )
    reserved = runner.reserve_group(
        created.execution_group_id, buy_expected_price=Decimal("10")
    )
    assert reserved.reserved_quote == Decimal("1001.000")
    assert reserved.reserved_base == 100
    balances = {row["asset"]: row for row in runner.store.list_balances("paper-default")}
    assert Decimal(balances["USDT"]["reserved"]) == Decimal("1001.000")
    assert Decimal(balances["BTC"]["reserved"]) == 100


def test_100_100_fill_completes_with_required_fill_contract(
    runner: ExecutionOrchestrator,
) -> None:
    execution = group(runner)
    buy = runner.submit_fill(
        execution.execution_group_id,
        "buy",
        book(Venue.BINANCE),
        Decimal("10"),
        "buy-100",
    )
    sell = runner.submit_fill(
        execution.execution_group_id,
        "sell",
        book(Venue.BYBIT),
        Decimal("11"),
        "sell-100",
    )
    completed = runner.group(execution.execution_group_id)
    assert completed.status == ExecutionGroupStatus.COMPLETED
    assert completed.residual_qty == 0
    assert completed.reservations_active is False
    assert all(
        Decimal(balance["reserved"]) == 0
        for balance in runner.store.list_balances("paper-default")
    )
    assert buy.execution_group_id == sell.execution_group_id == completed.execution_group_id
    assert buy.venue_order_id is None
    assert buy.simulated_order_id
    assert buy.requested_qty == buy.filled_qty == Decimal("100")
    assert buy.remaining_qty == 0
    assert buy.average_fill_price == Decimal("10")
    assert buy.fee == Decimal("1")
    assert buy.slippage == 0
    assert buy.timestamp and buy.status == "filled"
    assert {event["event"] for event in runner.store.list_audit()} >= {
        "signal",
        "approved",
        "pause",
    }


@pytest.mark.parametrize(
    ("buy_qty", "sell_qty", "expected"),
    (("100", "60", "40"), ("60", "100", "-40")),
)
def test_asymmetric_fills_require_hedge(
    runner: ExecutionOrchestrator, buy_qty: str, sell_qty: str, expected: str
) -> None:
    execution = group(runner)
    runner.submit_fill(
        execution.execution_group_id,
        "buy",
        book(Venue.BINANCE, ask_qty=buy_qty),
        Decimal("10"),
        "buy",
    )
    runner.submit_fill(
        execution.execution_group_id,
        "sell",
        book(Venue.BYBIT, bid_qty=sell_qty),
        Decimal("11"),
        "sell",
    )
    reconciled = runner.group(execution.execution_group_id)
    assert reconciled.residual_qty == Decimal(expected)
    assert reconciled.status == ExecutionGroupStatus.HEDGE_REQUIRED


def test_100_0_and_insufficient_liquidity_remain_hedge_required(
    runner: ExecutionOrchestrator,
) -> None:
    execution = group(runner)
    fill = runner.submit_fill(
        execution.execution_group_id,
        "buy",
        book(Venue.BINANCE, ask_qty="60"),
        Decimal("10"),
        "buy-partial",
    )
    assert fill.filled_qty == 60
    assert fill.remaining_qty == 40
    assert fill.status == "partially_filled"
    reconciled = runner.group(execution.execution_group_id)
    assert reconciled.buy_filled_qty == 60
    assert reconciled.sell_filled_qty == 0
    assert reconciled.residual_qty == 60
    assert reconciled.status == ExecutionGroupStatus.HEDGE_REQUIRED


def test_duplicate_event_and_multiple_partial_events_are_idempotent(
    runner: ExecutionOrchestrator,
) -> None:
    execution = group(runner)
    first = runner.submit_fill(
        execution.execution_group_id,
        "buy",
        book(Venue.BINANCE, ask_qty="60"),
        Decimal("10"),
        "same-event",
    )
    duplicate = runner.submit_fill(
        execution.execution_group_id,
        "buy",
        book(Venue.BINANCE, ask_qty="60"),
        Decimal("10"),
        "same-event",
    )
    second = runner.submit_fill(
        execution.execution_group_id,
        "buy",
        book(Venue.BINANCE, ask_qty="40"),
        Decimal("10"),
        "second-event",
    )
    assert duplicate == first
    assert second.requested_qty == second.filled_qty == 40
    recovered = runner.group(execution.execution_group_id)
    assert recovered.buy_filled_qty == 100
    assert len(recovered.fills) == 2
    assert len(runner.store.list_fills()) == 2


def test_duplicate_event_with_different_payload_is_rejected(
    runner: ExecutionOrchestrator,
) -> None:
    execution = group(runner)
    runner.submit_fill(
        execution.execution_group_id,
        "buy",
        book(Venue.BINANCE),
        Decimal("10"),
        "immutable-event",
    )
    with pytest.raises(ValueError, match="different payload"):
        runner.submit_fill(
            execution.execution_group_id,
            "buy",
            book(Venue.OKX),
            Decimal("10"),
            "immutable-event",
        )


def test_protective_hedge_closes_only_residual_and_can_fill_partially(
    runner: ExecutionOrchestrator,
) -> None:
    execution = group(runner)
    runner.submit_fill(
        execution.execution_group_id,
        "buy",
        book(Venue.BINANCE, ask_qty="60"),
        Decimal("10"),
        "buy-60",
    )
    runner.submit_fill(
        execution.execution_group_id,
        "sell",
        book(Venue.BYBIT, bid_qty="20"),
        Decimal("11"),
        "sell-20",
    )
    first = runner.protective_hedge(
        execution.execution_group_id,
        book(Venue.OKX, bid_qty="25"),
        "hedge-25",
    )
    assert first.requested_qty == 40
    assert first.filled_qty == 25
    assert first.remaining_qty == 15
    assert first.is_hedge is True
    partial = runner.group(execution.execution_group_id)
    assert partial.residual_qty == 15
    assert partial.status == ExecutionGroupStatus.HEDGE_REQUIRED
    final = runner.protective_hedge(
        execution.execution_group_id,
        book(Venue.OKX, bid_qty="100"),
        "hedge-final",
    )
    assert final.requested_qty == final.filled_qty == 15
    assert runner.group(execution.execution_group_id).status == ExecutionGroupStatus.COMPLETED


def test_full_hedge_reconciles_and_survives_restart(runner: ExecutionOrchestrator) -> None:
    execution = group(runner)
    runner.submit_fill(
        execution.execution_group_id,
        "buy",
        book(Venue.BINANCE),
        Decimal("10"),
        "buy-before-restart",
    )
    restarted = ExecutionOrchestrator(runner.store, clock_ms=lambda: NOW)
    assert restarted.group(execution.execution_group_id).residual_qty == 100
    hedge = restarted.protective_hedge(
        execution.execution_group_id,
        book(Venue.OKX),
        "hedge-after-restart",
    )
    assert hedge.requested_qty == hedge.filled_qty == 100
    assert restarted.group(execution.execution_group_id).status == ExecutionGroupStatus.COMPLETED


def test_hedge_failure_halts_runtime_until_explicit_reset(
    runner: ExecutionOrchestrator,
) -> None:
    execution = group(runner)
    runner.submit_fill(
        execution.execution_group_id,
        "buy",
        book(Venue.BINANCE),
        Decimal("10"),
        "buy-before-failure",
    )
    unavailable = replace(book(Venue.OKX), bids=())
    with pytest.raises(ExecutionHaltedError, match="HALTED"):
        runner.protective_hedge(execution.execution_group_id, unavailable, "failed-hedge")
    assert runner.group(execution.execution_group_id).status == ExecutionGroupStatus.HALTED
    assert runner.halted is True
    restarted = ExecutionOrchestrator(runner.store, clock_ms=lambda: NOW)
    with pytest.raises(ExecutionHaltedError, match="recovery/reset"):
        restarted.create_group(
            "BTC/USDT",
            Venue.BINANCE,
            Venue.BYBIT,
            Decimal("1"),
            risk_decision=APPROVED,
        )
    restarted.reset_after_recovery(actor="test-operator")
    assert restarted.halted is False
    assert restarted.create_group(
        "BTC/USDT",
        Venue.BINANCE,
        Venue.BYBIT,
        Decimal("1"),
        risk_decision=APPROVED,
    )


@pytest.mark.parametrize(("usdt", "btc", "side"), (("1", "100", "buy"), ("1000", "0", "sell")))
def test_negative_balance_is_prevented(tmp_path, usdt: str, btc: str, side: str) -> None:
    store = SQLitePaperStore(tmp_path / f"{side}.db")
    store.seed_account(
        balances={"USDT": Decimal(usdt), "BTC": Decimal(btc), "ETH": Decimal(0)}
    )
    runner = ExecutionOrchestrator(store, clock_ms=lambda: NOW)
    execution = runner.create_group(
        "BTC/USDT",
        Venue.BINANCE,
        Venue.BYBIT,
        Decimal("1"),
        risk_decision=APPROVED,
    )
    before = store.list_balances("paper-default")
    balance_kind = "base" if side == "sell" else "quote"
    with pytest.raises(ValueError, match=f"{balance_kind} balance"):
        runner.reserve_group(
            execution.execution_group_id,
            buy_expected_price=Decimal("10"),
        )
    assert store.list_fills() == []
    assert store.list_balances("paper-default") == before


def test_completed_with_residual_is_a_hard_invariant(runner: ExecutionOrchestrator) -> None:
    execution = group(runner)
    runner.submit_fill(
        execution.execution_group_id,
        "buy",
        book(Venue.BINANCE),
        Decimal("10"),
        "buy-residual",
    )
    with pytest.raises(ValueError, match="residual exposure"):
        runner.complete_group(execution.execution_group_id)
    assert runner.group(execution.execution_group_id).status != ExecutionGroupStatus.COMPLETED


def test_depth_vwap_fee_slippage_and_accounting_reconcile(
    runner: ExecutionOrchestrator,
) -> None:
    execution = runner.create_group(
        "BTC/USDT",
        Venue.BINANCE,
        Venue.BYBIT,
        Decimal("2"),
        risk_decision=APPROVED,
    )
    execution = runner.reserve_group(
        execution.execution_group_id, buy_expected_price=Decimal("10")
    )
    buy_book = normalize_order_book(
        Venue.BINANCE,
        "BTC/USDT",
        [["9", "10"]],
        [["10", "1"], ["11", "1"]],
        NOW,
        NOW,
    )
    fill = runner.submit_fill(
        execution.execution_group_id,
        "buy",
        buy_book,
        Decimal("10"),
        "depth-vwap",
    )
    assert fill.average_fill_price == Decimal("10.5")
    assert fill.fee == Decimal("0.021")
    assert fill.slippage == Decimal("5.00")
    runner.protective_hedge(
        execution.execution_group_id,
        book(Venue.OKX),
        "depth-hedge",
    )
    account = runner.store.get_account("paper-default")
    assert account is not None
    assert Decimal(account["fees_paid_usd"]) == Decimal("0.043")
    assert Decimal(account["realized_pnl_usd"]) == Decimal("0.957")
    assert PersistentPaperService(runner.store).reconcile()["issues"] == []


def test_hedge_idempotency_conflict_and_completed_precondition_do_not_halt(
    runner: ExecutionOrchestrator,
) -> None:
    first = group(runner)
    second = group(runner)
    runner.submit_fill(
        first.execution_group_id,
        "buy",
        book(Venue.BINANCE),
        Decimal("10"),
        "first-buy",
    )
    runner.submit_fill(
        second.execution_group_id,
        "buy",
        book(Venue.BINANCE),
        Decimal("10"),
        "occupied-event",
    )
    with pytest.raises(ValueError, match="different payload"):
        runner.protective_hedge(
            first.execution_group_id,
            book(Venue.OKX),
            "occupied-event",
        )
    assert runner.halted is False
    runner.protective_hedge(first.execution_group_id, book(Venue.OKX), "first-hedge")
    with pytest.raises(ValueError, match="does not require"):
        runner.protective_hedge(first.execution_group_id, book(Venue.OKX), "extra-hedge")
    assert runner.halted is False


@pytest.mark.parametrize(
    "kwargs",
    ({"live_trading_enabled": True}, {"trading_mode": "live"}),
)
def test_orchestrator_cannot_start_outside_locked_paper_mode(tmp_path, kwargs) -> None:
    store = SQLitePaperStore(tmp_path / "locked.db")
    store.seed_account()
    with pytest.raises(ValueError, match="paper mode"):
        ExecutionOrchestrator(store, **kwargs)


@pytest.mark.parametrize("offset", (-1_001, 1))
def test_stale_and_future_books_are_rejected(
    runner: ExecutionOrchestrator, offset: int
) -> None:
    execution = group(runner)
    valid = book(Venue.BINANCE)
    invalid = (
        replace(valid, timestamp_ms=NOW + offset, received_at_ms=NOW)
        if offset > 0
        else replace(valid, timestamp_ms=NOW + offset, received_at_ms=NOW + offset)
    )
    expected = "Future" if offset > 0 else "Stale"
    with pytest.raises(ValueError, match=expected):
        runner.submit_fill(
            execution.execution_group_id,
            "buy",
            invalid,
            Decimal("10"),
            f"timestamp-{offset}",
        )
