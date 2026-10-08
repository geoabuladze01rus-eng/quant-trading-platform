from importlib import import_module

import httpx
import pytest

from quant_trading_platform.config import Settings


class CachedService:
    def __init__(self, rows):
        self.rows = rows

    def snapshot(self):
        return self.rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,rows,status,problem",
    [
        (False, [], "disabled", None),
        (True, [], "not_ready", "fresh_spot_data_unavailable"),
        (True, [{"status": "ok", "worker_running": False}], "not_ready",
         "market_worker_stopped"),
        (True, [{"status": "ok", "worker_running": True}], "ready", None),
        (True, [{"status": "ok", "worker_running": True,
                 "observer_error": "market_observer_unavailable"}], "degraded",
         "market_observer_unavailable"),
        (True, [{"status": "ok", "worker_running": True},
                {"status": "error", "worker_running": True}], "degraded",
         "spot_source_unavailable_or_stale"),
    ],
)
async def test_readiness_distinguishes_liveness_workers_and_data(
    monkeypatch, enabled, rows, status, problem,
):
    api = import_module("quant_trading_platform.api.app")
    monkeypatch.setattr(api, "settings", Settings(
        _env_file=None, public_market_data_enabled=enabled, derivatives_data_enabled=False,
    ))
    monkeypatch.setattr(api.app.state, "market_data", CachedService(rows), raising=False)
    monkeypatch.setattr(api.app.state, "derivatives", None, raising=False)
    monkeypatch.setattr(api.app.state, "spot_signals", CachedService(
        {"status": "ok", "worker_running": True},
    ), raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Readiness must never perform upstream IO or writes")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test",
    ) as client:
        assert (await client.get("/health")).json()["status"] == "ok"
        response = await client.get("/readiness")
        assert response.json()["status"] == status
        if problem is not None:
            assert problem in response.json()["problems"]
        assert response.json()["live_execution"] is False
        assert (await client.post("/readiness")).status_code == 405
