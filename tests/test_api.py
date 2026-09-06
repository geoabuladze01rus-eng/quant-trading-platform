from collections.abc import AsyncIterator
from dataclasses import replace
from decimal import Decimal
from importlib import import_module

import httpx
import pytest
import pytest_asyncio

from quant_trading_platform.audit_log import AuditLog
from quant_trading_platform.config import MarketScope, Settings
from quant_trading_platform.models import MarketQuote, MarketType, Venue

api = import_module("quant_trading_platform.api.app")


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(api, "_QUOTE_CACHE", {})
    monkeypatch.setattr(api, "audit_log", AuditLog())
    monkeypatch.setattr(api, "settings", Settings(_env_file=None))
    monkeypatch.setattr(api, "now_ms", lambda: 10_000)
    monkeypatch.setattr("quant_trading_platform.risk.time", lambda: 10)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as session:
        yield session


def cache_quotes(timestamp: int = 10_000) -> None:
    for venue, bid, ask in [(Venue.BINANCE, "99", "100"), (Venue.BYBIT, "102", "103")]:
        api._QUOTE_CACHE[(venue.value, "BTC/USDT")] = MarketQuote(
            venue, "BTC/USDT", Decimal(bid), Decimal(ask), Decimal("0.1"),
            Decimal("0.1"), timestamp,
        )


@pytest.mark.asyncio
async def test_dashboard_endpoints_are_available_and_safe(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json()["live_trading"] == "locked"
    assert (await client.get("/settings")).json()["trading_mode"] == "paper"
    venues = (await client.get("/venues")).json()
    assert len(venues) == 4
    assert all(venue["live_execution"] is False for venue in venues)
    assert (await client.get("/risk")).json()["live_trading_locked"] is True
    response = await client.get("/opportunities")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "no_data", "opportunities": []}
    assert (await client.get("/audit")).json() == []


@pytest.mark.asyncio
async def test_fresh_opportunity_contract_and_risk(client: httpx.AsyncClient) -> None:
    cache_quotes()
    payload = (await client.get("/opportunities")).json()
    assert payload["status"] == "ok"
    item = next(x for x in payload["opportunities"] if x["buy_venue"] == "binance")
    assert item["approved"] is True
    assert item["data_age_ms"] == 0
    assert Decimal(item["expected_net_pct"]) == Decimal("1.75")
    assert set(item) == {
        "strategy", "symbol", "buy_venue", "sell_venue", "gross_spread_pct", "fees_pct",
        "slippage_pct", "expected_net_pct", "max_notional_usd", "approved", "reason",
        "data_age_ms",
    }
    assert api.audit_log.list() == []


@pytest.mark.asyncio
async def test_stale_age_passed_to_risk(client: httpx.AsyncClient) -> None:
    cache_quotes(timestamp=8_000)
    items = (await client.get("/opportunities")).json()["opportunities"]
    assert items
    assert all(not item["approved"] for item in items)
    assert all(item["data_age_ms"] == 2_000 for item in items)
    assert all("stale" in item["reason"].lower() for item in items)


@pytest.mark.asyncio
async def test_audit_get_is_read_only(client: httpx.AsyncClient) -> None:
    api.audit_log.record("rejected", "Test rejection", MarketScope.CRYPTO, "spread")
    before = api.audit_log.list()
    for _ in range(3):
        assert (await client.get("/audit")).json() == before
    assert api.audit_log.list() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["symbol", "market"])
async def test_incompatible_quotes_do_not_create_candidates(
    client: httpx.AsyncClient, mismatch: str,
) -> None:
    cache_quotes()
    quote = api._QUOTE_CACHE[("bybit", "BTC/USDT")]
    api._QUOTE_CACHE[("bybit", "BTC/USDT")] = (
        replace(quote, symbol="ETH/USDT") if mismatch == "symbol"
        else replace(quote, market_type=MarketType.RUSSIAN_STOCKS)
    )
    assert (await client.get("/opportunities")).json() == {
        "status": "no_data", "opportunities": [],
    }


@pytest.mark.asyncio
async def test_local_frontend_can_read_api(client: httpx.AsyncClient) -> None:
    response = await client.get("/venues", headers={"Origin": "http://localhost:5173"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.asyncio
async def test_paper_flag_cannot_advertise_live_unlock(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "settings", Settings(_env_file=None, live_trading_enabled=True))
    assert (await client.get("/risk")).json()["live_trading_locked"] is True
    assert all(not item["live_execution"] for item in (await client.get("/venues")).json())
