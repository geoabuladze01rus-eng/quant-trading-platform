from collections.abc import AsyncIterator
from importlib import import_module

import httpx
import pytest
import pytest_asyncio

from quant_trading_platform.market_data.intraday_research import IntradayResearchService

api = import_module("quant_trading_platform.api.app")


class NoopSource:
    def fetch(self, symbol: str, *, now_ms: int, bar: str) -> tuple[object, ...]:
        raise ValueError("not used in this test")

    def close(self) -> None:
        pass


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # ASGITransport does not run the lifespan, so app.state starts without research wiring.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as session:
        yield session


@pytest.mark.asyncio
async def test_intraday_research_is_disabled_by_default(client: httpx.AsyncClient) -> None:
    if hasattr(api.app.state, "intraday_research"):
        delattr(api.app.state, "intraday_research")
    body = (await client.get("/strategies/intraday-research")).json()
    assert body["status"] == "disabled"
    assert body["paper_only"] is True
    assert body["live_execution"] is False
    assert body["candidates"] == []


@pytest.mark.asyncio
async def test_intraday_research_returns_cached_snapshot_without_fetching(
    client: httpx.AsyncClient,
) -> None:
    service = IntradayResearchService(NoopSource())  # type: ignore[arg-type]
    api.app.state.intraday_research = service
    try:
        body = (await client.get("/strategies/intraday-research")).json()
        assert body["status"] == "no_data"
        assert body["live_execution"] is False
    finally:
        api.app.state.intraday_research = None
