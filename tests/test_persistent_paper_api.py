"""Durable HTTP contract: local paper commands only, exact-once across restart."""

from collections.abc import AsyncIterator
from decimal import Decimal
from importlib import import_module

import httpx
import pytest
import pytest_asyncio

from quant_trading_platform.audit_log import AuditLog, PersistentAuditLog
from quant_trading_platform.config import Settings
from quant_trading_platform.execution_orchestrator import ExecutionOrchestrator
from quant_trading_platform.market_data.models import normalize_order_book
from quant_trading_platform.models import Venue
from quant_trading_platform.paper_trading.service import PersistentPaperService
from quant_trading_platform.persistence import SQLitePaperStore
from quant_trading_platform.risk import RiskDecision

api = import_module("quant_trading_platform.api.app")
INTENT = {
    "symbol": "BTCUSDT",
    "buy_venue": "binance",
    "sell_venue": "okx",
    "notional_usdt": "100",
}


class Books:
    def __init__(self, depth: str = "10") -> None:
        self.books = {
            Venue.BINANCE: normalize_order_book(
                Venue.BINANCE,
                "BTC/USDT",
                [["99", depth]],
                [["100", depth]],
                10_000,
                10_000,
            ),
            Venue.OKX: normalize_order_book(
                Venue.OKX,
                "BTC/USDT",
                [["102", depth]],
                [["103", depth]],
                10_000,
                10_000,
            ),
        }

    def book_for_simulation(self, venue: Venue, symbol: str):
        book = self.books.get(venue)
        return book if book is not None and symbol == "BTC/USDT" else None


@pytest_asyncio.fixture
async def durable_client(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, SQLitePaperStore]]:
    store = SQLitePaperStore(tmp_path / "paper.db")
    store.seed_account(
        balances={"USDT": Decimal("10000"), "BTC": Decimal("10"), "ETH": Decimal("0")}
    )
    monkeypatch.setattr(api, "settings", Settings(_env_file=None))
    monkeypatch.setattr(api, "audit_log", AuditLog())
    monkeypatch.setattr(api, "now_ms", lambda: 10_000)
    monkeypatch.setattr("quant_trading_platform.risk.time", lambda: 10)
    monkeypatch.setattr("quant_trading_platform.paper_trading.time", lambda: 10)
    monkeypatch.setattr(api.app.state, "market_data", Books(), raising=False)
    orchestrator = ExecutionOrchestrator(store, clock_ms=lambda: 10_000)
    monkeypatch.setattr(
        api.app.state,
        "paper_service",
        PersistentPaperService(store, orchestrator=orchestrator),
        raising=False,
    )
    monkeypatch.setattr(
        api.app.state, "persistent_audit", PersistentAuditLog(store), raising=False
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        yield client, store
    store.close()


@pytest.mark.asyncio
async def test_durable_command_replays_without_double_debit_or_fill(durable_client) -> None:
    client, store = durable_client
    headers = {"Idempotency-Key": "create-one"}
    first = await client.post("/paper/orders", json=INTENT, headers=headers)
    second = await client.post("/paper/orders", json=INTENT, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["paper_only"] is True
    assert first.json()["live_trading_enabled"] is False
    assert len(store.list_orders()) == 1
    assert len(store.list_fills()) == 2
    assert len(store.list_audit()) == 3
    required_audit = {
        "event_id",
        "timestamp",
        "actor",
        "actor_type",
        "event_type",
        "strategy",
        "symbol",
        "venue",
        "market_type",
        "order_id",
        "opportunity_id",
        "decision",
        "reason_code",
        "human_reason",
        "risk_score",
        "gross_edge",
        "fees",
        "slippage",
        "net_edge",
        "data_age_ms",
        "correlation_id",
        "algorithm_version",
    }
    assert all(required_audit <= event.keys() for event in store.list_audit())


@pytest.mark.asyncio
async def test_http_idempotency_survives_service_restart(durable_client) -> None:
    client, store = durable_client
    response = await client.post(
        "/paper/orders", json=INTENT, headers={"Idempotency-Key": "restart-key"}
    )
    assert response.status_code == 200
    api.app.state.paper_service = PersistentPaperService(store)
    replay = await client.post(
        "/paper/orders", json=INTENT, headers={"Idempotency-Key": "restart-key"}
    )
    assert replay.json() == response.json()
    assert len(store.list_orders()) == 1


@pytest.mark.asyncio
async def test_conflicting_key_and_missing_key_are_safely_rejected(durable_client) -> None:
    client, store = durable_client
    headers = {"Idempotency-Key": "same-key"}
    assert (await client.post("/paper/orders", json=INTENT, headers=headers)).status_code == 200
    conflict = await client.post(
        "/paper/orders", json={**INTENT, "notional_usdt": "99"}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["reason_code"] == "duplicate_idempotency_key"
    assert (await client.post("/paper/orders", json=INTENT)).status_code == 422
    assert len(store.list_orders()) == 1


@pytest.mark.asyncio
async def test_accounting_views_and_audit_filters_are_read_only(durable_client) -> None:
    client, store = durable_client
    created = await client.post(
        "/paper/orders", json=INTENT, headers={"Idempotency-Key": "views"}
    )
    order_id = created.json()["order"]["id"]
    for path in (
        "/paper/account",
        "/paper/balances",
        "/paper/orders",
        f"/paper/orders/{order_id}",
        "/paper/fills",
        "/paper/positions",
        "/paper/performance",
        "/paper/reconciliation",
    ):
        assert (await client.get(path)).status_code == 200
    before = store.count_audit()
    page = (await client.get(f"/audit?limit=2&order_id={order_id}")).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert all(event["order_id"] == order_id for event in page["items"])
    assert store.count_audit() == before


@pytest.mark.asyncio
async def test_execution_group_observability_is_read_only_and_discloses_residual(
    durable_client,
) -> None:
    client, store = durable_client
    orchestrator = api.app.state.paper_service.execution_orchestrator
    created = orchestrator.create_group(
        "BTC/USDT",
        Venue.BINANCE,
        Venue.OKX,
        Decimal("1"),
        risk_decision=RiskDecision(True, "Approved for paper lifecycle", ("paper_only",)),
    )
    orchestrator.reserve_group(created.execution_group_id, buy_expected_price=Decimal("100"))
    orchestrator.submit_fill(
        created.execution_group_id,
        "buy",
        api.app.state.market_data.books[Venue.BINANCE],
        Decimal("100"),
        "api-observed-fill",
    )
    before = (len(store.list_orders()), len(store.list_fills()), store.count_audit())
    runtime = await client.get("/paper/execution-runtime")
    listing = await client.get("/paper/execution-groups")
    detail = await client.get(f"/paper/execution-groups/{created.execution_group_id}")
    missing = await client.get("/paper/execution-groups/missing")
    assert runtime.status_code == listing.status_code == detail.status_code == 200
    assert runtime.json()["status"] == "ACTIVE"
    assert runtime.json()["hedge_required"] == 1
    assert listing.json()[0]["paper_only"] is True
    assert listing.json()[0]["live_execution"] is False
    assert detail.json()["status"] == "HEDGE_REQUIRED"
    assert detail.json()["residual_qty"] == "1"
    assert detail.json()["fills"][0]["venue_order_id"] is None
    assert detail.json()["reconciliation"]["ok"] is True
    assert missing.status_code == 404
    assert (len(store.list_orders()), len(store.list_fills()), store.count_audit()) == before


@pytest.mark.asyncio
async def test_partial_order_cancel_releases_reserves(durable_client) -> None:
    client, store = durable_client
    api.app.state.market_data = Books("0.5")
    created = await client.post(
        "/paper/orders", json=INTENT, headers={"Idempotency-Key": "partial"}
    )
    assert created.json()["status"] == "partially_filled"
    assert any(Decimal(row["reserved"]) > 0 for row in created.json()["balances"])
    order_id = created.json()["order"]["id"]
    cancelled = await client.post(
        f"/paper/orders/{order_id}/cancel",
        headers={"Idempotency-Key": "cancel-partial"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert all(Decimal(row["reserved"]) == 0 for row in cancelled.json()["balances"])
    assert store.get_order(order_id)["status"] == "cancelled"


@pytest.mark.asyncio
async def test_live_setting_still_produces_only_rejected_local_record(
    durable_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = durable_client
    monkeypatch.setattr(api, "settings", Settings(_env_file=None, live_trading_enabled=True))
    response = await client.post(
        "/paper/orders", json=INTENT, headers={"Idempotency-Key": "live-is-locked"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["reason_code"] == "live_trading_locked"
    assert store.list_fills() == []
