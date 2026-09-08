"""Full API path: public depth -> independent gates -> paper records -> audit."""

from collections.abc import AsyncIterator
from dataclasses import replace
from decimal import Decimal
from importlib import import_module

import httpx
import pytest
import pytest_asyncio

from quant_trading_platform.audit_log import AuditLog
from quant_trading_platform.config import Settings
from quant_trading_platform.market_data.models import NormalizedOrderBook, normalize_order_book
from quant_trading_platform.market_data.service import MarketDataService
from quant_trading_platform.models import Venue
from quant_trading_platform.paper_trading import PaperExecutionEngine

api = import_module("quant_trading_platform.api.app")
INTENT = {
    "symbol": "BTCUSDT", "buy_venue": "binance", "sell_venue": "okx", "notional_usd": "10",
}


class Source:
    def __init__(self, venue: Venue, bid: int, ask: int) -> None:
        self.venue = venue
        self.book = normalize_order_book(
            venue, "BTC/USDT", [[bid, 10]], [[ask, 10]], 10_000, 10_000,
        )
        self.error = False

    def get_order_book(self, symbol: str) -> NormalizedOrderBook:
        if self.error:
            raise OSError("upstream failure")
        return self.book

    def close(self) -> None:
        pass


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(api, "settings", Settings(_env_file=None))
    monkeypatch.setattr(api, "paper_engine", PaperExecutionEngine())
    monkeypatch.setattr(api, "audit_log", AuditLog())
    monkeypatch.setattr(api, "_QUOTE_CACHE", {})
    monkeypatch.setattr(api, "_LAST_SIGNALS", {})
    monkeypatch.setattr(api, "now_ms", lambda: 10_000)
    monkeypatch.setattr("quant_trading_platform.risk.time", lambda: 10)
    monkeypatch.setattr("quant_trading_platform.paper_trading.time", lambda: 10)
    sources = [Source(Venue.BINANCE, 99, 100), Source(Venue.OKX, 102, 103)]
    service = MarketDataService(
        sources, api._QUOTE_CACHE, clock=lambda: 10_000,
        on_update=api.record_detected_opportunities,
    )
    monkeypatch.setattr(api.app.state, "market_data", service, raising=False)
    await service.poll_once()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test",
    ) as session:
        yield session
    await service.stop()


@pytest.mark.asyncio
async def test_simulation_and_reconciliation_never_call_connector_orders(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def prohibited(*args: object, **kwargs: object) -> None:
        pytest.fail("Paper path must never call a connector order method")

    monkeypatch.setattr(
        "quant_trading_platform.connectors.crypto.client.PublicCryptoConnector.place_order",
        prohibited,
    )
    monkeypatch.setattr(
        "quant_trading_platform.connectors.t_invest.client.TInvestClient.place_order", prohibited,
    )
    result = await client.post("/paper/orders/simulate", json=INTENT)
    assert result.status_code == 200
    report = result.json()
    assert report["status"] == "filled"
    assert report["reason_code"] and report["reason_text"]
    assert report["explanation"]["paper_only"] is True
    assert len(report["orders"]) == len(report["fills"]) == 2
    assert {fill["execution_id"] for fill in report["fills"]} == {report["execution_id"]}
    assert all(fill["reason_text"] for fill in report["fills"])
    rec = report["reconciliation"]
    assert Decimal(rec["expected_net_pct"]) == Decimal("1.75")
    assert Decimal(rec["simulated_net_pct"]) == Decimal("1.748")
    assert Decimal(rec["fees_pct"]) == Decimal("0.202")
    assert Decimal(rec["slippage_pct"]) == Decimal("0.05")
    assert rec["data_age_ms"] == 0
    assert len((await client.get("/paper/orders")).json()) == 2
    assert len((await client.get("/paper/fills")).json()) == 2
    events = (await client.get("/audit")).json()
    execution_events = [e for e in events if e["execution_id"] == report["execution_id"]]
    assert {e["event"] for e in execution_events} == {
        "opportunity_detected", "risk_approved", "paper_order_created", "paper_fill_simulated",
        "reconciliation_completed",
    }
    assert len(execution_events) == 6
    assert all(e["who"] == "local_paper_user" and e["why"] for e in execution_events)
    before = api.audit_log.list()
    for _ in range(2):
        for path in ("/audit", "/opportunities?explain=true", "/paper/orders", "/paper/fills",
                     "/reconciliation", "/venues"):
            assert (await client.get(path)).status_code == 200
    assert api.audit_log.list() == before


@pytest.mark.asyncio
async def test_explainable_opt_in_preserves_legacy_contract(client: httpx.AsyncClient) -> None:
    legacy = (await client.get("/opportunities")).json()["opportunities"]
    extended = (await client.get("/opportunities?explain=true")).json()["opportunities"]
    assert len(legacy) == len(extended) == 2
    item = next(i for i in extended if i["buy_venue"] == "binance")
    assert item["approved"] and item["risk_score"] == "passed"
    assert item["simulation_notional_usd"] == "10"
    assert item["summary"] and item["reason_code"] and item["reason_text"]
    assert "summary" not in legacy[0]
    # Identical producer snapshots do not duplicate signal audit records.
    before = api.audit_log.list()
    await api.app.state.market_data.poll_once()
    assert api.audit_log.list() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("case,code", [
    ("stale", "stale_market_data"), ("missing", "venue_unavailable"),
    ("error", "venue_unavailable"), ("edge", "insufficient_edge_after_costs"),
    ("notional", "notional_limit_exceeded"), ("live", "live_trading_locked"),
    ("scope", "market_type_mismatch"),
])
async def test_rejected_post_has_no_orders_or_fills(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, case: str, code: str,
) -> None:
    service = api.app.state.market_data
    state = service.states[Venue.OKX]
    intent = dict(INTENT)
    if case == "stale":
        state.book = replace(state.book, timestamp_ms=8000, received_at_ms=8000)
    elif case == "missing":
        state.book = None
    elif case == "error":
        state.status = "error"  # Cached last-good depth MUST NOT execute.
    elif case == "edge":
        state.book = normalize_order_book(
            Venue.OKX, "BTC/USDT", [[100, 10]], [[101, 10]], 10000, 10000,
        )
    elif case == "notional":
        intent["notional_usd"] = "101"
    elif case == "live":
        monkeypatch.setattr(api, "settings", Settings(_env_file=None, live_trading_enabled=True))
    elif case == "scope":
        monkeypatch.setattr(
            api, "settings", Settings(_env_file=None, market_scope="russian_stocks"),
        )
    response = await client.post("/paper/orders/simulate", json=intent)
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == "rejected" and report["reason_code"] == code
    assert not report["orders"] and not report["fills"]
    assert (await client.get("/paper/fills")).json() == []
    assert "paper_order_rejected" in {event["event"] for event in api.audit_log.list()}
    if case == "live":
        candidates = (await client.get("/opportunities?explain=true")).json()["opportunities"]
        assert all(not item["approved"] for item in candidates)
        assert all(item["reason_code"] == "live_trading_locked" for item in candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"notional_usd": "NaN"}, {"notional_usd": "Infinity"}, {"notional_usd": "0"},
    {"notional_usd": "-1"}, {"approved": True}, {"fees_pct": "0"},
    {"trading_mode": "live"}, {"symbol": "BTC/USDT/ETH"},
])
async def test_post_rejects_invalid_or_forged_intents(
    client: httpx.AsyncClient, overrides: dict[str, object],
) -> None:
    before = api.audit_log.list()
    response = await client.post("/paper/orders/simulate", json={**INTENT, **overrides})
    assert response.status_code == 422
    assert not api.paper_engine.reports
    assert api.audit_log.list() == before


@pytest.mark.asyncio
async def test_post_browser_origins_and_only_paper_route(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/paper/orders/simulate", json=INTENT, headers={"Origin": "https://untrusted.test"},
    )
    assert response.status_code == 403
    assert not api.paper_engine.reports
    preflight = await client.options("/paper/orders/simulate", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert preflight.status_code == 200
    schema = (await client.get("/openapi.json")).json()
    assert [p for p, methods in schema["paths"].items() if "post" in methods] == [
        "/paper/orders/simulate",
    ]


@pytest.mark.asyncio
async def test_explicit_size_uses_multiple_depth_levels(client: httpx.AsyncClient) -> None:
    service = api.app.state.market_data
    service.states[Venue.BINANCE].book = normalize_order_book(
        Venue.BINANCE, "BTC/USDT", [[99, 10]], [[100, "0.02"], [101, "1"]], 10000, 10000,
    )
    report = (await client.post("/paper/orders/simulate", json=INTENT)).json()
    assert report["status"] == "filled"
    buy_fill = next(fill for fill in report["fills"] if fill["side"] == "buy")
    assert Decimal(buy_fill["price"]) > 100
    assert Decimal(report["reconciliation"]["depth_slippage_pct"]) > 0
    assert Decimal(report["reconciliation"]["slippage_cost_usd"]) == Decimal("0.005")


@pytest.mark.asyncio
@pytest.mark.parametrize("thin_venue", [Venue.BINANCE, Venue.OKX])
async def test_insufficient_depth_rejects_both_legs(
    client: httpx.AsyncClient, thin_venue: Venue,
) -> None:
    service = api.app.state.market_data
    book = service.states[thin_venue].book
    service.states[thin_venue].book = replace(
        book, bids=tuple(replace(level, quantity=Decimal("0.001")) for level in book.bids),
        asks=tuple(replace(level, quantity=Decimal("0.001")) for level in book.asks),
    )
    report = (await client.post("/paper/orders/simulate", json=INTENT)).json()
    assert report["status"] == "rejected"
    assert report["reason_code"] == "insufficient_depth"
    assert not report["orders"] and not report["fills"]
