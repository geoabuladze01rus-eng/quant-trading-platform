"""Spot-only paper accounting and safety boundaries."""

from decimal import Decimal
from importlib import import_module
from time import time

import httpx
import pytest

from quant_trading_platform.config import Settings
from quant_trading_platform.market_data.models import normalize_order_book
from quant_trading_platform.models import Venue
from quant_trading_platform.paper_trading.models import PaperCommandError
from quant_trading_platform.paper_trading.okx_spot import OKXSpotPaperService
from quant_trading_platform.persistence.store import SQLitePaperStore


def book(symbol: str = "LTC/USDT", bid: str = "99", ask: str = "100"):
    now = int(time() * 1000)
    return normalize_order_book(
        Venue.OKX, symbol, [[bid, "10"]], [[ask, "10"]], now, now
    )


def service(tmp_path):
    store = SQLitePaperStore(tmp_path / "okx.db")
    store.seed_account("okx-test", {
        "USDT": Decimal("10000"), "BTC": Decimal(0),
        "ETH": Decimal(0), "LTC": Decimal(0),
    })
    store.upsert_account("okx-test", {"account_kind": "okx_spot"})
    instance = OKXSpotPaperService(store)
    assert instance.recover("okx-test")["status"] == "ok"
    return instance


def trade(instance, side="buy", key="first", market=None, amount="100", symbol="LTC/USDT",
          settings=None):
    return instance.execute_spot(
        symbol=symbol, side=side, notional_usdt=Decimal(amount),
        book=market if market is not None else book(symbol),
        settings=settings or Settings(_env_file=None),
        idempotency_key=key, account_id="okx-test",
    )


def test_buy_sell_exactly_once_and_independent_reconciliation(tmp_path):
    instance = service(tmp_path)
    bought = trade(instance)
    assert bought["accounting_reconciliation"]["status"] == "ok"
    assert bought["order"]["side"] == "buy"
    assert bought["fills"][0]["venue"] == "okx"
    assert bought["paper_only"] and not bought["live_execution"]
    assert bought["account"]["realized_pnl_usd"] == "0"
    assert trade(instance) == bought
    sold = trade(instance, "sell", "second", book(bid="99", ask="100"), amount="99")
    assert sold["accounting_reconciliation"]["status"] == "ok"
    assert Decimal(sold["account"]["realized_pnl_usd"]) < 0
    assert sold["account"]["spot_inventory"]["LTC"]["quantity"] == "0"
    assert len(instance.store.list_orders("okx-test")) == 2
    assert len(instance.store.list_fills("okx-test")) == 2
    assert len(instance.store.list_reconciliations("okx-test")) == 3
    instance.store.close()


def test_missing_inventory_and_exposure_cap_do_not_create_orders(tmp_path):
    instance = service(tmp_path)
    with pytest.raises(PaperCommandError) as no_inventory:
        trade(instance, "sell")
    assert no_inventory.value.reason_code == "insufficient_paper_balance"
    for index in range(4):
        trade(instance, key=f"buy-{index}")
    with pytest.raises(PaperCommandError) as cap:
        trade(instance, key="cap")
    assert cap.value.reason_code == "notional_limit_exceeded"
    assert len(instance.store.list_orders("okx-test")) == 4
    instance.store.close()


def test_stale_or_wrong_venue_and_live_mode_are_blocked(tmp_path):
    instance = service(tmp_path)
    old = normalize_order_book(
        Venue.OKX, "LTC/USDT", [["99", "10"]], [["100", "10"]], 10000, 10000
    )
    with pytest.raises(PaperCommandError) as stale:
        trade(instance, market=old)
    assert stale.value.reason_code == "stale_market_data"
    live = Settings(_env_file=None, trading_mode="live")
    with pytest.raises(PaperCommandError) as locked:
        trade(instance, key="live", settings=live)
    assert locked.value.reason_code == "live_trading_locked"
    assert not instance.store.list_orders("okx-test")
    instance.store.close()


def test_daily_realized_loss_halts_new_spot_commands(tmp_path):
    instance = service(tmp_path)
    for index in range(4):
        trade(instance, key=f"buy-{index}")
    exit_result = trade(
        instance, "sell", "exit", book(bid="20", ask="21"), amount="80"
    )
    assert Decimal(exit_result["account"]["spot_daily_pnl_usdt"]) < -200
    assert exit_result["accounting_reconciliation"]["status"] == "ok"
    with pytest.raises(PaperCommandError) as stopped:
        trade(instance, key="after-loss")
    assert stopped.value.reason_code == "daily_loss_limit_reached"
    assert len(instance.store.list_orders("okx-test")) == 5
    instance.store.close()


def test_audit_failure_rolls_back_spot_balances_and_idempotency(tmp_path, monkeypatch):
    instance = service(tmp_path)
    before = instance.account("okx-test")

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(instance.store, "insert_audit", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        trade(instance)
    assert instance.account("okx-test") == before
    assert instance.store.list_orders("okx-test") == []
    assert instance.store.get_idempotency("okx-test", "first") is None
    instance.store.close()


@pytest.mark.asyncio
async def test_http_spot_command_uses_only_server_owned_okx_book(tmp_path, monkeypatch):
    api = import_module("quant_trading_platform.api.app")
    instance = service(tmp_path)
    settings = Settings(_env_file=None, okx_spot_paper_account_id="okx-test")
    monkeypatch.setattr(api, "settings", settings)
    monkeypatch.setattr(api.app.state, "okx_spot_service", instance, raising=False)

    class Books:
        def book_for_simulation(self, venue, symbol):
            assert venue == Venue.OKX and symbol == "LTC/USDT"
            return book()

    monkeypatch.setattr(api.app.state, "market_data", Books(), raising=False)
    intent = {"symbol": "LTC-USDT", "side": "buy", "notional_usdt": "100"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        untrusted = await client.post(
            "/paper/okx/orders", json=intent,
            headers={"Origin": "https://untrusted.test", "Idempotency-Key": "spot-1"},
        )
        assert untrusted.status_code == 403
        first = await client.post(
            "/paper/okx/orders", json=intent, headers={"Idempotency-Key": "spot-1"}
        )
        repeat = await client.post(
            "/paper/okx/orders", json=intent, headers={"Idempotency-Key": "spot-1"}
        )
        assert first.status_code == repeat.status_code == 200
        assert first.json() == repeat.json()
        assert first.json()["accounting_reconciliation"]["status"] == "ok"
        assert (await client.get("/paper/okx/account")).json()["account_kind"] == "okx_spot"
    assert len(instance.store.list_fills("okx-test")) == 1
    instance.store.close()
