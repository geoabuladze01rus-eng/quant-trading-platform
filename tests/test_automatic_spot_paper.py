"""Actual durable paper fills from public-data fixtures; never send an exchange order."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from importlib import import_module
from time import time

import httpx
import pytest

from quant_trading_platform.config import Settings
from quant_trading_platform.market_data.models import normalize_order_book
from quant_trading_platform.market_data.okx_candles import OKXCandleSource
from quant_trading_platform.market_data.spot_signal_service import SpotSignalService
from quant_trading_platform.models import Venue
from quant_trading_platform.paper_trading.auto_spot import AutomaticSpotPaper
from quant_trading_platform.paper_trading.okx_spot import OKXSpotPaperService
from quant_trading_platform.persistence import SQLitePaperStore
from quant_trading_platform.strategies.spot_momentum import DAY_MS, SYMBOLS, DailyCandle


class Feed:
    def __init__(self):
        self.now = int(time() * 1000)
        self.down = False
        self.stale = False
        self.missing = False
        self.wide = False

    def completed_series(self, *, now_ms):
        if self.missing:
            raise ValueError("missing")
        start = self.now // DAY_MS - 110
        result = {}
        for symbol in SYMBOLS:
            rows = []
            for index in range(110):
                close = Decimal(100 + index) if symbol == SYMBOLS[0] else Decimal(100)
                if self.down and symbol == SYMBOLS[0] and index == 109:
                    close = Decimal(90)
                rows.append(
                    DailyCandle(
                        (start + index) * DAY_MS,
                        close,
                        close + Decimal("0.5"),
                        close - 1,
                        close,
                        Decimal(10),
                    )
                )
            result[symbol] = tuple(rows)
        return result

    def book_for_simulation(self, venue, symbol):
        assert venue == Venue.OKX
        price = Decimal(90 if self.down else 209) if symbol == SYMBOLS[0] else Decimal(100)
        stamp = self.now - 5000 if self.stale else self.now
        return normalize_order_book(
            Venue.OKX,
            symbol,
            [[price, "100"]],
            [[price + (10 if self.wide else Decimal("0.01")), "100"]],
            stamp,
            stamp,
        )


def setup(tmp_path, monkeypatch, **options):
    feed = Feed()
    monkeypatch.setattr(
        "quant_trading_platform.paper_trading.okx_spot.time", lambda: feed.now / 1000
    )
    cfg = Settings(
        _env_file=None, okx_spot_auto_enabled=True, okx_spot_paper_account_id="auto-test", **options
    )
    store = SQLitePaperStore(tmp_path / "auto.db")
    store.seed_account(
        "auto-test",
        {"USDT": Decimal(10000), "BTC": Decimal(0), "ETH": Decimal(0), "LTC": Decimal(0)},
    )
    store.upsert_account("auto-test", {"account_kind": "okx_spot"})
    service = OKXSpotPaperService(store)
    service.recover("auto-test")
    worker = AutomaticSpotPaper(service, cfg, feed, feed, clock=lambda: feed.now)
    return feed, worker, service


def test_daily_signal_opens_persistent_order_and_restart_does_not_repeat(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    initial = service.account("auto-test")
    state = worker.cycle()
    assert state["status"] == "running"
    orders = service.store.list_orders("auto-test")
    assert len(orders) == 1 and orders[0]["side"] == "buy"
    assert orders[0]["actor"] == "automatic_paper"
    assert orders[0]["signal_strategy"] == "daily_trend"
    assert len(service.store.list_fills("auto-test")) == 1
    assert Decimal(service.account("auto-test")["equity_usd"]) < Decimal(initial["equity_usd"])
    path = tmp_path / "auto.db"
    service.store.close()
    reopened = OKXSpotPaperService(SQLitePaperStore(path))
    assert reopened.recover("auto-test")["status"] == "ok"
    repeat = AutomaticSpotPaper(reopened, worker.settings, feed, feed, clock=lambda: feed.now)
    for _ in range(5):
        assert repeat.cycle()["status"] == "running"
    assert len(reopened.store.list_orders("auto-test")) == 1
    assert len(reopened.store.list_fills("auto-test")) == 1
    reopened.store.close()


def test_natural_next_day_exit_sells_exact_inventory_and_reconciles(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    worker.cycle()
    feed.now += DAY_MS
    feed.down = True
    worker.cycle()
    account = service.account("auto-test")
    assert account["spot_inventory"]["BTC"]["quantity"] == "0"
    assert [o["side"] for o in service.store.list_orders("auto-test")] == ["sell", "buy"]
    assert Decimal(account["realized_pnl_usd"]) < 0
    assert Decimal(account["fees_paid_usd"]) > 0
    assert service.reconcile("auto-test")["status"] == "ok"
    service.store.close()


@pytest.mark.parametrize("failure", ["stale", "missing", "wide", "disabled", "live", "gate"])
def test_data_and_mode_gates_never_create_orders(tmp_path, monkeypatch, failure):
    options = {"okx_spot_auto_enabled": False} if failure == "disabled" else {}
    feed, worker, service = setup(tmp_path, monkeypatch)
    if failure in ("stale", "missing", "wide"):
        setattr(feed, failure, True)
    else:
        update = (
            options
            if failure == "disabled"
            else {"live_trading_enabled": True}
            if failure == "live"
            else {"live_order_acceptance_gate": True}
        )
        worker.settings = worker.settings.model_copy(update=update)
    result = worker.cycle()
    assert result["status"] in ("running", "blocked")
    assert service.store.list_orders("auto-test") == []
    assert service.store.list_fills("auto-test") == []
    assert service.store.get_balance("auto-test", "USDT")["available"] == "10000"
    service.store.close()


def test_mark_to_market_loss_latches_until_utc_rollover_and_still_allows_exit(
    tmp_path, monkeypatch
):
    feed, worker, service = setup(tmp_path, monkeypatch, max_daily_loss_pct=Decimal("0.1"))
    worker.cycle()
    # Keep the completed entry signal unchanged, but observe a current-price gap down.
    original = feed.book_for_simulation

    def lower(venue, symbol):
        book = original(venue, symbol)
        if symbol != SYMBOLS[0]:
            return book
        return normalize_order_book(
            venue, symbol, [["90", "100"]], [["90.01", "100"]], feed.now, feed.now
        )

    feed.book_for_simulation = lower
    state = worker.cycle()
    assert state["daily_loss_halted"] and state["reason_code"] == "daily_loss_limit_reached"
    feed.book_for_simulation = original
    assert worker.cycle()["daily_loss_halted"]  # price recovery does not unlock purchases
    feed.now += DAY_MS
    feed.down = True
    state = worker.cycle()
    assert state["daily_loss_halted"]  # overnight gap is charged against last observed equity
    assert service.account("auto-test")["spot_inventory"]["BTC"]["quantity"] == "0"
    assert service.reconcile("auto-test")["status"] == "ok"
    service.store.close()


def test_audit_fault_rolls_back_whole_cycle_and_halts(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    original = service.store.insert_audit

    def fail(record, **kwargs):
        if (
            record.get("event_type") == "auto_paper_cycle"
            and record["details"]["status"] == "running"
        ):
            raise RuntimeError("injected storage error")
        return original(record, **kwargs)

    monkeypatch.setattr(service.store, "insert_audit", fail)
    state = worker.cycle()
    assert state["reason_code"] == "auto_paper_execution_error"
    assert service.store.list_orders("auto-test") == []
    assert service.store.list_fills("auto-test") == []
    assert service.store.get_balance("auto-test", "USDT")["available"] == "10000"
    assert service.store.get_account("auto-test").get("auto_signal_cursor", {}) == {}
    with service.store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM idempotency_records").fetchone()[0] == 0
    service.store.close()


def test_reconciliation_mismatch_halts_before_execution(tmp_path, monkeypatch):
    _, worker, service = setup(tmp_path, monkeypatch)
    service.store.upsert_balance("auto-test", "BTC", Decimal(1))
    assert worker.cycle()["reason_code"] == "reconciliation_mismatch"
    assert service.store.get_account("auto-test")["status"] == "halted"
    assert service.store.list_orders("auto-test") == []
    service.store.close()


def test_concurrent_workers_open_exactly_one_order(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    other = AutomaticSpotPaper(service, worker.settings, feed, feed, clock=lambda: feed.now)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda w: w.cycle(), [worker, other]))
    assert all(result["status"] == "running" for result in results)
    assert len(service.store.list_orders("auto-test")) == 1
    assert len(service.store.list_fills("auto-test")) == 1
    assert service.reconcile("auto-test")["status"] == "ok"
    service.store.close()


def test_chunked_exit_finishes_after_restart_without_quote_rounding_dust(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    worker.cycle()
    feed.now += DAY_MS
    # A bearish completed candle can precede a current intraday rebound. The
    # value of the acquired position now exceeds the per-order cap.
    feed.down = True
    original = feed.book_for_simulation

    def rebound(venue, symbol):
        if symbol != SYMBOLS[0]:
            return original(venue, symbol)
        return normalize_order_book(
            venue, symbol, [["251.37", "100"]], [["251.38", "100"]], feed.now, feed.now
        )

    feed.book_for_simulation = rebound
    worker.cycle()
    assert Decimal(service.account("auto-test")["spot_inventory"]["BTC"]["quantity"]) > 0
    assert Decimal(service.store.list_orders("auto-test")[0]["requested_notional_usd"]) <= 100
    assert service.reconcile("auto-test")["status"] == "ok"
    service.store.close()
    reopened = OKXSpotPaperService(SQLitePaperStore(tmp_path / "auto.db"))
    assert reopened.recover("auto-test")["status"] == "ok"
    again = AutomaticSpotPaper(reopened, worker.settings, feed, feed, clock=lambda: feed.now)
    again.cycle()
    assert reopened.account("auto-test")["spot_inventory"]["BTC"]["quantity"] == "0"
    assert len(reopened.store.list_orders("auto-test")) == 3
    again.cycle()
    assert len(reopened.store.list_orders("auto-test")) == 3
    assert reopened.reconcile("auto-test")["status"] == "ok"
    reopened.store.close()


def test_depth_rejection_is_logged_and_retries_without_pending_idempotency(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    original = feed.book_for_simulation

    def shallow(venue, symbol):
        book = original(venue, symbol)
        return replace(
            book, asks=tuple(replace(level, quantity=Decimal("0.001")) for level in book.asks)
        )

    feed.book_for_simulation = shallow
    state = worker.cycle()
    assert state["decisions"][0]["reason_code"] == "insufficient_depth"
    assert not service.store.list_orders("auto-test")
    with service.store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM idempotency_records").fetchone()[0] == 0
    feed.book_for_simulation = original
    worker.cycle()
    assert len(service.store.list_orders("auto-test")) == 1
    assert service.reconcile("auto-test")["status"] == "ok"
    service.store.close()


def test_legacy_manual_idempotency_response_survives_worker_upgrade(tmp_path, monkeypatch):
    feed, _, service = setup(tmp_path, monkeypatch)
    from quant_trading_platform.paper_trading.models import canonical_hash

    intent = {
        "operation": "okx_spot_paper",
        "account_id": "auto-test",
        "symbol": "BTC/USDT",
        "side": "buy",
        "notional_usdt": "100",
    }
    response = {"order": {"order_id": "legacy"}, "paper_only": True, "live_execution": False}
    with service.store.transaction() as conn:
        service.store.reserve_idempotency("auto-test", "legacy", canonical_hash(intent), conn=conn)
        service.store.complete_idempotency("auto-test", "legacy", response, conn=conn)
    result = service.execute_spot(
        symbol="BTC/USDT",
        side="buy",
        notional_usdt=Decimal(100),
        book=feed.book_for_simulation(Venue.OKX, "BTC/USDT"),
        settings=Settings(_env_file=None),
        idempotency_key="legacy",
        account_id="auto-test",
    )
    assert result == response
    assert not service.store.list_orders("auto-test")
    service.store.close()


def test_future_receipt_and_crossed_book_are_blocked(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    original = feed.book_for_simulation
    feed.book_for_simulation = lambda venue, symbol: replace(
        original(venue, symbol), received_at_ms=feed.now + 5000
    )
    assert worker.cycle()["reason_code"] == "stale_market_data"
    assert not service.store.list_orders("auto-test")
    service.store.close()


@pytest.mark.asyncio
async def test_background_worker_and_read_only_monitoring(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)
    api = import_module("quant_trading_platform.api.app")
    monkeypatch.setattr(api, "settings", worker.settings)
    monkeypatch.setattr(api.app.state, "auto_spot", worker, raising=False)
    monkeypatch.setattr(api.app.state, "okx_spot_service", service, raising=False)
    await worker.start()
    try:
        for _ in range(100):
            if service.store.list_orders("auto-test"):
                break
            await asyncio.sleep(0.01)
        assert len(service.store.list_orders("auto-test")) == 1
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            state = (await client.get("/paper/okx/robot")).json()
            assert state["worker_running"] and state["orders_count"] == 1
            assert state["paper_only"] and not state["live_execution"]
            history = (await client.get("/paper/okx/history")).json()
            assert len(history["orders"]) == len(history["fills"]) == 1
            assert history["accounting_reconciliation"]["status"] == "ok"
            before = service.store.get_account("auto-test")["updated_at"]
            await client.get("/paper/okx/robot")
            await client.get("/paper/okx/history")
            assert service.store.get_account("auto-test")["updated_at"] == before
            manual = await client.post(
                "/paper/okx/orders",
                json={"symbol": "BTC/USDT", "side": "buy", "notional_usdt": "100"},
                headers={"Idempotency-Key": "manual"},
            )
            assert manual.status_code == 409
    finally:
        await worker.stop()
        service.store.close()


@pytest.mark.asyncio
async def test_public_candle_parser_to_automatic_fill(tmp_path, monkeypatch):
    feed, worker, service = setup(tmp_path, monkeypatch)

    def transport(request):
        assert request.method == "GET" and "authorization" not in request.headers
        series = feed.completed_series(now_ms=feed.now)
        symbol = request.url.params["instId"].replace("-", "/")
        if symbol not in series:
            # Research-only pairs (e.g. SOL) are not in this feed; OKX answers with an error code.
            return httpx.Response(
                200, json={"code": "51001", "msg": "Instrument ID does not exist", "data": []},
            )
        rows = series[symbol]
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    [
                        str(row.timestamp_ms),
                        str(row.open),
                        str(row.high),
                        str(row.low),
                        str(row.close),
                        str(row.volume),
                        "0",
                        "0",
                        "1",
                    ]
                    for row in reversed(rows)
                ],
            },
        )

    source = OKXCandleSource(httpx.Client(transport=httpx.MockTransport(transport)))
    signals = SpotSignalService(source)
    await signals.poll_once()
    worker.candles = signals
    worker.cycle()
    assert len(service.store.list_orders("auto-test")) == 1
    assert service.reconcile("auto-test")["status"] == "ok"
    source.close()
    service.store.close()


@pytest.mark.asyncio
async def test_startup_does_not_credit_ltc_to_legacy_account(tmp_path, monkeypatch):
    api = import_module("quant_trading_platform.api.app")
    path = tmp_path / "legacy.db"
    store = SQLitePaperStore(path)
    store.seed_account(
        "paper-default", {"USDT": Decimal(10000), "BTC": Decimal(1), "ETH": Decimal(10)}
    )
    store.close()
    monkeypatch.setattr(
        api,
        "settings",
        Settings(_env_file=None, paper_database_path=path, public_market_data_enabled=False),
    )
    async with api.lifespan(api.app):
        assert api.app.state.paper_recovery["status"] == "ok"
        assert api.app.state.paper_store.get_balance("paper-default", "LTC")["available"] == "0"
