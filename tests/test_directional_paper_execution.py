from dataclasses import replace
from decimal import Decimal
from time import time

import pytest

from quant_trading_platform.config import Settings
from quant_trading_platform.market_data.models import normalize_order_book
from quant_trading_platform.models import Venue
from quant_trading_platform.paper_trading.service import PersistentPaperService
from quant_trading_platform.persistence import SQLitePaperStore


def book(price: str):
    now = int(time() * 1000)
    value = Decimal(price)
    return normalize_order_book(
        Venue.BYBIT,
        "BTC/USDT",
        [[value, "10"]],
        [[value, "10"]],
        now,
        now,
    )


def service(tmp_path):
    store = SQLitePaperStore(tmp_path / "directional.db")
    store.seed_account(
        balances={
            "USDT": Decimal("1000"),
            "BTC": Decimal("1"),
            "ETH": Decimal("0"),
            "LTC": Decimal("0"),
        }
    )
    return PersistentPaperService(store)


def execute(instance, *, side: str, price: str, key: str, live: bool = False):
    return instance.execute_spot(
        symbol="BTC/USDT",
        venue=Venue.BYBIT,
        side=side,
        book=book(price),
        quantity=Decimal("0.1"),
        fee_rate_pct=Decimal("0.1"),
        slippage_pct=Decimal("0.05"),
        expected_net_edge_pct=Decimal("0.5"),
        settings=Settings(_env_file=None, live_trading_enabled=live),
        idempotency_key=key,
    )


def test_directional_round_trip_uses_only_strategy_inventory_and_reconciles(tmp_path) -> None:
    instance = service(tmp_path)
    bought = execute(instance, side="buy", price="100", key="buy")
    assert bought["status"] == "filled"
    assert bought["order"]["side"] == "buy"
    assert instance.reconcile()["status"] == "ok"

    sold = execute(instance, side="sell", price="110", key="sell")
    assert sold["status"] == "filled"
    assert Decimal(sold["order"]["realized_pnl_usd"]) == Decimal("0.9685")
    account = instance.account()
    assert Decimal(account["strategy_realized_pnl_usdt"]) == Decimal("0.9685")
    assert Decimal(account["strategy_inventory"]["BTC"]["quantity"]) == 0
    assert Decimal(next(row for row in account["balances"] if row["asset"] == "BTC")["total"]) == 1
    assert instance.reconcile()["status"] == "ok"


def test_directional_sell_cannot_consume_prefunded_inventory(tmp_path) -> None:
    instance = service(tmp_path)
    before = instance.account()["balances"]
    result = execute(instance, side="sell", price="110", key="sell-seed")
    assert result["status"] == "rejected"
    assert result["reason_code"] == "strategy_position_unavailable"
    assert result["fills"] == []
    assert instance.account()["balances"] == before
    assert instance.reconcile()["status"] == "ok"


def test_directional_execution_is_idempotent_and_live_locked(tmp_path) -> None:
    instance = service(tmp_path)
    first = execute(instance, side="buy", price="100", key="same")
    assert execute(instance, side="buy", price="100", key="same") == first
    assert len(instance.store.list_fills()) == 1

    blocked = execute(instance, side="buy", price="100", key="live", live=True)
    assert blocked["status"] == "rejected"
    assert blocked["reason_code"] == "live_trading_locked"
    assert len(instance.store.list_fills()) == 1


def test_directional_execution_rejects_insufficient_balance_without_negative_values(
    tmp_path,
) -> None:
    instance = service(tmp_path)
    result = instance.execute_spot(
        symbol="BTC/USDT",
        venue=Venue.BYBIT,
        side="buy",
        book=book("100"),
        quantity=Decimal("20"),
        fee_rate_pct=Decimal("0.1"),
        slippage_pct=Decimal("0.05"),
        expected_net_edge_pct=Decimal("0.5"),
        settings=Settings(_env_file=None),
        idempotency_key="too-large",
    )
    assert result["status"] == "rejected"
    assert result["fills"] == []
    assert all(
        Decimal(row[field]) >= 0
        for row in instance.account()["balances"]
        for field in ("available", "reserved")
    )


@pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USDT", "LTC/USDT"])
def test_each_requested_asset_uses_directional_paper_accounting(tmp_path, symbol) -> None:
    instance = service(tmp_path)
    now = int(time() * 1000)
    source = normalize_order_book(
        Venue.BYBIT,
        symbol,
        [["100", "10"]],
        [["100", "10"]],
        now,
        now,
    )
    result = instance.execute_spot(
        symbol=symbol,
        venue=Venue.BYBIT,
        side="buy",
        book=source,
        quantity=Decimal("0.1"),
        fee_rate_pct=Decimal("0.1"),
        slippage_pct=Decimal("0.05"),
        expected_net_edge_pct=Decimal("0.5"),
        settings=Settings(_env_file=None),
        idempotency_key=f"buy-{symbol}",
    )
    asset = symbol.split("/")[0]
    assert result["status"] == "filled"
    assert Decimal(result["account"]["strategy_inventory"][asset]["quantity"]) == Decimal(
        "0.1"
    )
    assert instance.reconcile()["status"] == "ok"


def test_directional_executor_rechecks_current_book_freshness(tmp_path) -> None:
    instance = service(tmp_path)
    stale = replace(
        book("100"),
        timestamp_ms=int(time() * 1000) - 10_000,
        received_at_ms=int(time() * 1000) - 10_000,
    )
    result = instance.execute_spot(
        symbol="BTC/USDT",
        venue=Venue.BYBIT,
        side="buy",
        book=stale,
        quantity=Decimal("0.1"),
        fee_rate_pct=Decimal("0.1"),
        slippage_pct=Decimal("0.05"),
        expected_net_edge_pct=Decimal("0.5"),
        settings=Settings(_env_file=None),
        idempotency_key="stale",
    )
    assert result["status"] == "rejected"
    assert result["reason_code"] == "stale_market_data"
    assert result["fills"] == []
