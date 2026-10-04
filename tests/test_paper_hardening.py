"""Operational and economic integrity regressions for the automatic paper runner."""

from dataclasses import replace
from decimal import Decimal
from importlib import import_module

import httpx
import pytest
from test_automatic_spot_paper import setup

from quant_trading_platform.config import Settings
from quant_trading_platform.market_data.models import normalize_order_book
from quant_trading_platform.models import Venue
from quant_trading_platform.paper_trading.auto_spot import AutomaticSpotPaper
from quant_trading_platform.paper_trading.models import PaperCommandError
from quant_trading_platform.paper_trading.okx_spot import OKXSpotPaperService
from quant_trading_platform.paper_trading.reconciliation import reconcile_records
from quant_trading_platform.persistence import SQLitePaperStore


@pytest.mark.parametrize(
    "price,reason", [("300", "entry_price_deviation"), ("90", "trend_invalidated")]
)
def test_completed_breakout_cannot_buy_after_current_price_gap(
    tmp_path, monkeypatch, price, reason
):
    feed, worker, service = setup(tmp_path, monkeypatch)
    original = feed.book_for_simulation

    def gap(venue, symbol):
        if symbol != "BTC/USDT":
            return original(venue, symbol)
        bid = Decimal(price)
        return normalize_order_book(
            venue, symbol, [[bid, "100"]], [[bid + Decimal("0.01"), "100"]], feed.now, feed.now
        )

    feed.book_for_simulation = gap
    state = worker.cycle()
    assert state["decisions"][0]["reason_code"] == reason
    assert not service.store.list_orders("auto-test")
    assert service.store.get_balance("auto-test", "USDT")["available"] == "10000"
    service.store.close()


def test_quote_cost_updates_do_not_flood_decision_audit(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    worker.cycle()
    worker.cycle()  # transition from fill to processed
    before = len(service.store.list_audit("auto-test", event_type="auto_paper_cycle"))
    original = feed.book_for_simulation
    for index in range(30):

        def changed(venue, symbol, index=index):
            book = original(venue, symbol)
            return replace(
                book,
                asks=tuple(
                    replace(level, price=level.price + Decimal(index) / 1000) for level in book.asks
                ),
            )

        feed.book_for_simulation = changed
        worker.cycle()
    assert len(service.store.list_audit("auto-test", event_type="auto_paper_cycle")) == before
    assert len(service.store.list_orders("auto-test")) == 1
    service.store.close()


def test_pause_resume_is_durable_idempotent_and_does_not_reset_signal_cursor(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    paused = worker.control("pause", idempotency_key="pause-1")
    assert worker.control("pause", idempotency_key="pause-1") == paused
    assert worker.cycle()["status"] == "paused"
    assert not service.store.list_orders("auto-test")
    service.store.close()
    reopened = OKXSpotPaperService(SQLitePaperStore(tmp_path / "auto.db"))
    assert reopened.recover("auto-test")["status"] == "ok"
    again = AutomaticSpotPaper(reopened, worker.settings, feed, feed, clock=lambda: feed.now)
    assert again.snapshot()["status"] == "paused"
    assert again.cycle()["status"] == "paused"
    again.control("resume", idempotency_key="resume-1")
    again.cycle()
    assert len(reopened.store.list_orders("auto-test")) == 1
    cursor = reopened.store.get_account("auto-test")["auto_signal_cursor"]
    again.control("pause", idempotency_key="pause-2")
    again.control("resume", idempotency_key="resume-2")
    again.cycle()
    assert reopened.store.get_account("auto-test")["auto_signal_cursor"] == cursor
    assert len(reopened.store.list_orders("auto-test")) == 1
    reopened.store.close()


def test_pause_committed_during_data_collection_prevents_subsequent_fill(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    original = feed.book_for_simulation

    def interrupt(venue, symbol):
        if symbol == "BTC/USDT":
            worker.control("pause", idempotency_key="pause-during-collection")
        return original(venue, symbol)

    feed.book_for_simulation = interrupt
    assert worker.cycle()["status"] == "paused"
    assert not service.store.list_orders("auto-test")
    service.store.close()


def test_resume_does_not_unlock_corrupt_account_and_audit_fault_rolls_back_control(
    tmp_path, monkeypatch
):
    _, worker, service = setup(tmp_path, monkeypatch)
    worker.control("pause", idempotency_key="pause")
    service.store.upsert_balance("auto-test", "BTC", Decimal(1))
    with pytest.raises(PaperCommandError) as error:
        worker.control("resume", idempotency_key="corrupt-resume")
    assert error.value.reason_code == "reconciliation_mismatch"
    assert service.store.get_account("auto-test")["auto_operator_paused"]
    assert service.store.get_idempotency("auto-test", "corrupt-resume") is None
    service.store.upsert_balance("auto-test", "BTC", Decimal(0))

    def fail(*args, **kwargs):
        raise RuntimeError("audit disk failure")

    monkeypatch.setattr(service.store, "insert_audit", fail)
    with pytest.raises(RuntimeError):
        worker.control("resume", idempotency_key="failed-resume")
    assert service.store.get_account("auto-test")["auto_operator_paused"]
    assert service.store.get_idempotency("auto-test", "failed-resume") is None
    service.store.close()


@pytest.mark.parametrize(
    "damage,reason",
    [
        ("missing_position", "missing_spot_position"),
        ("cost_basis", "spot_cost_basis_mismatch"),
        ("position_cost", "spot_position_cost_mismatch"),
        ("fill_price", "spot_fill_price_mismatch"),
        ("fill_identity", "spot_fill_identity_mismatch"),
    ],
)
def test_independent_reconciliation_detects_corrupt_economics(
    tmp_path, monkeypatch, damage, reason
):
    _, worker, service = setup(tmp_path, monkeypatch)
    worker.cycle()
    account = service.store.get_account("auto-test")
    balances = service.store.list_balances("auto-test")
    orders = service.store.list_orders("auto-test")
    fills = service.store.list_fills("auto-test")
    positions = service.store.list_positions("auto-test")
    if damage == "missing_position":
        positions = [position for position in positions if position["asset"] != "BTC"]
    elif damage == "cost_basis":
        account["cost_basis"]["BTC"] = "1"
    elif damage == "position_cost":
        for position in positions:
            if position["asset"] == "BTC":
                position["cost_basis"] = "1"
    elif damage == "fill_price":
        fills[0]["price"] = "1"
    elif damage == "fill_identity":
        fills[0]["venue"] = "bybit"
    result = reconcile_records(account, balances, orders, fills, positions)
    assert result["status"] == "error"
    assert reason in {item["reason_code"] for item in result["issues"]}
    service.store.close()


def test_fractional_manual_costs_and_sales_replay_in_identical_decimal_order(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    for index in range(3):
        service.execute_spot(
            symbol="BTC/USDT",
            side="buy",
            notional_usdt=Decimal("19.12345678901234567890123456"),
            book=feed.book_for_simulation(Venue.OKX, "BTC/USDT"),
            settings=worker.settings,
            idempotency_key=f"fraction-{index}",
            account_id="auto-test",
        )
    held = Decimal(service.account("auto-test")["spot_inventory"]["BTC"]["quantity"])
    service.execute_spot(
        symbol="BTC/USDT",
        side="sell",
        notional_usdt=Decimal(100),
        sell_quantity=held,
        book=feed.book_for_simulation(Venue.OKX, "BTC/USDT"),
        settings=worker.settings,
        idempotency_key="fraction-exit",
        account_id="auto-test",
    )
    assert service.reconcile("auto-test")["status"] == "ok"
    assert service.account("auto-test")["spot_inventory"]["BTC"]["quantity"] == "0"
    service.store.close()


@pytest.mark.asyncio
async def test_control_http_origin_idempotency_and_body_validation(tmp_path, monkeypatch):
    _, worker, service = setup(tmp_path, monkeypatch)
    api = import_module("quant_trading_platform.api.app")
    monkeypatch.setattr(api.app.state, "auto_spot", worker, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        path = "/paper/okx/robot/control"
        assert (await client.post(path, json={"action": "pause"})).status_code == 422
        headers = {"Idempotency-Key": "http-pause"}
        assert (
            await client.post(
                path,
                json={"action": "pause"},
                headers={**headers, "Origin": "https://untrusted.test"},
            )
        ).status_code == 403
        first = await client.post(path, json={"action": "pause"}, headers=headers)
        repeat = await client.post(path, json={"action": "pause"}, headers=headers)
        assert first.status_code == 200 and first.json() == repeat.json()
        assert (
            await client.post(path, json={"action": "resume"}, headers=headers)
        ).status_code == 409
        assert (
            await client.post(
                path,
                json={"action": "resume", "live_execution": True},
                headers={"Idempotency-Key": "extra"},
            )
        ).status_code == 422
        assert len(service.store.list_audit("auto-test", event_type="auto_paper_control")) == 1
    service.store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configuration",
    [
        {"public_market_data_enabled": False},
        {"market_scope": "russian_stocks"},
        {"market_data_symbols": "BTC/USDT,ETH/USDT"},
    ],
)
async def test_incomplete_auto_feed_configuration_fails_before_creating_database(
    tmp_path,
    monkeypatch,
    configuration,
):
    api = import_module("quant_trading_platform.api.app")
    path = tmp_path / "should-not-exist.db"
    cfg = Settings(
        _env_file=None, okx_spot_auto_enabled=True, paper_database_path=path, **configuration
    )
    monkeypatch.setattr(api, "settings", cfg)
    with pytest.raises(ValueError, match="requires public crypto feeds"):
        async with api.lifespan(api.app):
            pytest.fail("Invalid automatic feed configuration accepted")
    assert not path.exists()
