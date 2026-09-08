from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from time import time

import pytest

from quant_trading_platform.config import Settings
from quant_trading_platform.market_data.models import OrderBookLevel, normalize_order_book
from quant_trading_platform.models import ArbitrageOpportunity, Venue
from quant_trading_platform.paper_trading import PaperEngine


def fixture():
    now = int(time() * 1000)
    opportunity = ArbitrageOpportunity(
        "spread",
        "BTC/USDT",
        Venue.BINANCE,
        Venue.BYBIT,
        Decimal("2"),
        Decimal("1.75"),
        Decimal("500"),
        now,
        fees_pct=Decimal(".20"),
        slippage_pct=Decimal(".05"),
        source_timestamp_ms=now,
    )
    buy = normalize_order_book(Venue.BINANCE, "BTC/USDT", [[99, 10]], [[100, 10]], now, now)
    sell = normalize_order_book(Venue.BYBIT, "BTC/USDT", [[102, 10]], [[103, 10]], now, now)
    return opportunity, buy, sell


def test_atomic_fill_actual_costs_and_immutable_records():
    opportunity, buy, sell = fixture()
    engine = PaperEngine()
    report = engine.simulate(opportunity, buy, sell, notional_usd=Decimal(100), settings=Settings())
    assert report.status == "filled"
    assert len(engine.orders) == len(engine.fills) == 2
    assert report.reconciliation.simulated_net_pct == Decimal("1.748")
    assert report.reconciliation.fees_pct == Decimal(".202")
    assert engine.positions[0].quantity == 0
    assert engine.positions[0].realized_pnl_usd == Decimal("1.748")
    with pytest.raises(FrozenInstanceError):
        report.status = "changed"


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "0", "101", "501"])
def test_invalid_or_oversize_request_never_fills(amount):
    opportunity, buy, sell = fixture()
    engine = PaperEngine()
    report = engine.simulate(
        opportunity, buy, sell, notional_usd=Decimal(amount), settings=Settings()
    )
    assert report.status == "rejected"
    assert not engine.fills and not engine.orders and not engine.positions


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "stale",
        "future",
        "identity",
        "depth",
        "bad_depth",
        "risk",
        "edge",
        "live",
        "scope",
        "capacity",
    ],
)
def test_rejections_are_atomic(case):
    opportunity, buy, sell = fixture()
    settings = Settings()
    if case == "missing":
        buy = None
    elif case == "stale":
        buy = replace(
            buy, timestamp_ms=buy.timestamp_ms - 2000, received_at_ms=buy.received_at_ms - 2000
        )
    elif case == "future":
        buy = replace(
            buy, timestamp_ms=buy.timestamp_ms + 10000, received_at_ms=buy.received_at_ms + 10000
        )
    elif case == "identity":
        buy = replace(buy, symbol="ETH/USDT")
    elif case == "depth":
        sell = replace(sell, bids=(OrderBookLevel(Decimal(102), Decimal(".1")),))
    elif case == "bad_depth":
        buy = replace(buy, asks=buy.asks + (OrderBookLevel(Decimal("NaN"), Decimal(1)),))
    elif case == "risk":
        opportunity = replace(opportunity, rejection_reason="paused")
    elif case == "edge":
        sell = replace(sell, bids=(OrderBookLevel(Decimal(100), Decimal(10)),))
    elif case == "live":
        settings = Settings(live_trading_enabled=True)
    elif case == "scope":
        settings = Settings(market_scope="russian_stocks")
    elif case == "capacity":
        opportunity = replace(opportunity, max_notional_usd=Decimal(50))
    engine = PaperEngine()
    report = engine.simulate(opportunity, buy, sell, notional_usd=Decimal(100), settings=settings)
    assert report.status == "rejected"
    assert not engine.fills and not engine.orders and not engine.positions
    assert engine.reports == (report,)


def test_multiple_depth_levels_change_realized_edge():
    opportunity, buy, sell = fixture()
    buy = replace(
        buy,
        asks=(
            OrderBookLevel(Decimal(100), Decimal(".5")),
            OrderBookLevel(Decimal(101), Decimal(1)),
        ),
    )
    report = PaperEngine().simulate(
        opportunity, buy, sell, notional_usd=Decimal(100), settings=Settings()
    )
    assert report.status == "filled"
    assert report.fills[0].quantity < 1
    assert report.reconciliation.simulated_net_pct < Decimal("1.748")


def test_canonical_names_timestamps_and_bounded_storage():
    from quant_trading_platform.paper_trading import PaperExecutionEngine, PaperExecutionReport

    engine = PaperExecutionEngine(max_records=2)
    opportunity, buy, sell = fixture()
    for _ in range(3):
        report = engine.simulate(
            opportunity, buy, sell, notional_usd=Decimal(100), settings=Settings()
        )
    assert isinstance(report, PaperExecutionReport)
    assert len(engine.reports) == len(engine.positions) == 2
    assert len(engine.orders) == len(engine.fills) == 4
    assert report.fills[0].timestamp_ms >= buy.timestamp_ms
    assert report.orders[0].timestamp_ms == report.fills[0].timestamp_ms
    assert report.fills[0].reason_text


@pytest.mark.parametrize("field", ["fees_pct", "slippage_pct", "expected_net_pct"])
def test_nonfinite_values_rejected_with_finite_reconciliation(field):
    opportunity, buy, sell = fixture()
    opportunity = replace(opportunity, **{field: Decimal("NaN")})
    report = PaperEngine().simulate(
        opportunity, buy, sell, notional_usd=Decimal(100), settings=Settings()
    )
    assert report.status == "rejected"
    assert all(
        value.is_finite()
        for value in (
            report.reconciliation.expected_net_pct,
            report.reconciliation.fees_pct,
            report.reconciliation.slippage_pct,
        )
    )


def test_expected_edge_reconstructed_from_current_books():
    opportunity, buy, sell = fixture()
    opportunity = replace(
        opportunity, expected_gross_pct=Decimal(999), expected_net_pct=Decimal(998)
    )
    report = PaperEngine().simulate(
        opportunity, buy, sell, notional_usd=Decimal(100), settings=Settings()
    )
    assert report.reconciliation.expected_net_pct == Decimal("1.75")


def test_fill_cash_flow_minus_reserve_reconciles_to_position():
    opportunity, buy, sell = fixture()
    engine = PaperEngine()
    report = engine.simulate(opportunity, buy, sell, notional_usd=Decimal(100), settings=Settings())
    buy_fill, sell_fill = report.fills
    assert (
        sell_fill.notional_usd
        - buy_fill.notional_usd
        - buy_fill.fee_usd
        - sell_fill.fee_usd
        - report.reconciliation.slippage_cost_usd
        == engine.positions[0].realized_pnl_usd
    )
    assert report.reconciliation.slippage_cost_usd == Decimal(".05")
    assert report.reconciliation.depth_slippage_pct == 0


@pytest.mark.parametrize("symbol", ["BTC/EUR", "ETH/BTC", "BTC/RUB"])
def test_non_usd_quote_rejected(symbol):
    opportunity, buy, sell = fixture()
    report = PaperEngine().simulate(
        replace(opportunity, symbol=symbol),
        replace(buy, symbol=symbol),
        replace(sell, symbol=symbol),
        notional_usd=Decimal(100),
        settings=Settings(),
    )
    assert report.reason_code == "market_type_mismatch"
    assert not report.fills
