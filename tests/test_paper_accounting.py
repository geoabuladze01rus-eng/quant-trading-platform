from dataclasses import replace
from decimal import Decimal
from time import time

import pytest

from quant_trading_platform.config import Settings
from quant_trading_platform.market_data.models import normalize_order_book
from quant_trading_platform.models import ArbitrageOpportunity, Venue
from quant_trading_platform.paper_trading.service import PersistentPaperService
from quant_trading_platform.persistence.store import SQLitePaperStore


def market(depth="10"):
    now = int(time() * 1000)
    opportunity = ArbitrageOpportunity(
        "spread",
        "BTC/USDT",
        Venue.BINANCE,
        Venue.BYBIT,
        Decimal(2),
        Decimal("1.75"),
        Decimal(500),
        now,
        fees_pct=Decimal(".2"),
        slippage_pct=Decimal(".05"),
        source_timestamp_ms=now,
    )
    buy = normalize_order_book(Venue.BINANCE, "BTC/USDT", [[99, depth]], [[100, depth]], now, now)
    sell = normalize_order_book(Venue.BYBIT, "BTC/USDT", [[102, depth]], [[103, depth]], now, now)
    return opportunity, buy, sell


def service(path, usdt="10000", btc="10"):
    store = SQLitePaperStore(path)
    store.seed_account(balances={"USDT": Decimal(usdt), "BTC": Decimal(btc), "ETH": Decimal(0)})
    return PersistentPaperService(store)


def execute(instance, *, key="first", depth="10", amount="100"):
    return instance.execute(
        *market(depth), notional_usd=Decimal(amount), settings=Settings(), idempotency_key=key
    )


def test_funded_execution_reconciles_fills_fees_reserve_and_equity(tmp_path):
    instance = service(tmp_path / "paper.db")
    result = execute(instance)
    assert result["status"] == "filled"
    balances = {row["asset"]: row for row in result["account"]["balances"]}
    assert Decimal(balances["USDT"]["total"]) == Decimal("10001.748")
    assert Decimal(balances["BTC"]["total"]) == 10
    assert all(Decimal(row["reserved"]) == 0 for row in balances.values())
    assert result["account"]["realized_pnl_usd"] == "1.748"
    assert result["account"]["fees_paid_usd"] == "0.202"
    assert result["account"]["slippage_paid_usd"] == "0.05"
    assert result["account"]["equity_usd"] == "11001.748"
    assert instance.reconcile()["issues"] == []
    assert len(instance.store.list_audit()) == 3


@pytest.mark.parametrize("usdt,btc", [("100", "10"), ("10000", ".5")])
def test_requires_both_prefunded_legs_including_costs(tmp_path, usdt, btc):
    instance = service(tmp_path / "paper.db", usdt, btc)
    before = instance.account()["balances"]
    result = execute(instance)
    assert result["reason_code"] == "insufficient_paper_balance"
    assert not instance.store.list_fills()
    assert instance.account()["balances"] == before


def test_partial_fill_retains_only_remaining_reserves_and_cancel_releases(tmp_path):
    instance = service(tmp_path / "paper.db")
    result = execute(instance, depth=".5")
    assert result["status"] == "partially_filled"
    order = result["order"]
    assert Decimal(order["filled_quantity"]) == Decimal(".5")
    assert Decimal(order["remaining_notional_usd"]) == 50
    assert Decimal(order["reserved_quote"]) == Decimal("50.075")
    assert Decimal(order["reserved_base"]) == Decimal(".5")
    assert len(instance.store.list_fills()) == 2
    assert Decimal(result["account"]["fees_paid_usd"]) == Decimal(".101")
    assert instance.reconcile()["issues"] == []
    cancelled = instance.cancel(result["order_id"], idempotency_key="cancel")
    assert cancelled["status"] == "cancelled"
    assert all(Decimal(row["reserved"]) == 0 for row in cancelled["account"]["balances"])
    assert instance.reconcile()["issues"] == []


def test_preview_has_no_durable_side_effects(tmp_path):
    instance = service(tmp_path / "paper.db")
    before = instance.account()
    preview = instance.preview(*market(".5"), notional_usd=Decimal(100), settings=Settings())
    assert preview["status"] == "partially_filled"
    assert instance.account() == before
    assert instance.store.list_orders() == instance.store.list_fills() == []
    assert instance.store.list_audit() == []
    assert instance.engine.reports == ()


def test_partial_cannot_bypass_requested_notional_gate(tmp_path):
    instance = service(tmp_path / "paper.db")
    result = execute(instance, depth=".5", amount="101")
    assert result["status"] == "rejected"
    assert not result["fills"]


def test_invalid_book_remains_rejection_not_accounting_error(tmp_path):
    instance = service(tmp_path / "paper.db")
    opportunity, buy, sell = market()
    result = instance.execute(
        opportunity,
        replace(buy, asks=()),
        sell,
        notional_usd=Decimal(100),
        settings=Settings(),
        idempotency_key="bad",
    )
    assert result["status"] == "rejected"
    assert not result["fills"]


def test_audit_failure_rolls_back_fills_balances_and_idempotency(tmp_path, monkeypatch):
    instance = service(tmp_path / "paper.db")
    before = instance.account()
    original = instance.store.insert_audit

    def fail_after_fills(record, **kwargs):
        if record["event_type"] == "paper_order_filled":
            raise RuntimeError("audit unavailable")
        return original(record, **kwargs)

    monkeypatch.setattr(instance.store, "insert_audit", fail_after_fills)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        execute(instance)
    assert instance.account() == before
    assert instance.store.list_fills() == instance.store.list_orders() == []
    assert instance.store.get_idempotency("paper-default", "first") is None


def test_unpriced_holdings_are_disclosed_without_fabricated_equity(tmp_path):
    instance = service(tmp_path / "paper.db")
    account = instance.account()
    assert account["equity_usd"] is None
    assert account["unpriced_assets"] == ["BTC"]
    assert account["priced_equity_usd"] == "10000"
