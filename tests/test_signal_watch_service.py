from decimal import Decimal as D

import httpx
import pytest
from test_signal_intelligence import NOW
from test_signal_watch_engine import frame

from quant_trading_platform.signal_watch.engine import WatchEngine
from quant_trading_platform.signal_watch.journal import Journal
from quant_trading_platform.signal_watch.service import PublicStructureSource, WatchService


def test_public_structure_requests_no_inherited_credentials():
    requests = []
    now = 120_000_000

    def handler(request):
        requests.append(request)
        duration = 60_000 if request.url.params["bar"] == "1m" else 3_600_000
        end = now // duration * duration
        rows = [
            [str(end - (i + 1) * duration), "100", "105", "95", "102", "10", "10", "10", "1"]
            for i in range(30)
        ]
        return httpx.Response(200, json={"code": "0", "data": rows})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "private", "OK-ACCESS-KEY": "private"},
        cookies={"session": "private"},
    )
    result = PublicStructureSource(client).fetch("BTC/USDT", now_ms=now)
    assert isinstance(result.close, D)
    assert result.timestamp_ms == now
    assert {r.url.params["bar"] for r in requests} == {"1m", "1H"}
    assert all(r.url.params["instId"] == "BTC-USDT" for r in requests)
    assert all(
        "authorization" not in r.headers
        and "cookie" not in r.headers
        and "ok-access-key" not in r.headers
        for r in requests
    )


@pytest.mark.parametrize("bad", ["missing", "stale", "future", "nan"])
def test_bad_candles_fail_closed(bad):
    now = 120_000_000

    def handler(request):
        duration = 60_000 if request.url.params["bar"] == "1m" else 3_600_000
        end = now // duration * duration
        rows = [
            [str(end - (i + 1) * duration), "100", "105", "95", "102", "10", "10", "10", "1"]
            for i in range(30)
        ]
        if bad == "missing":
            rows.pop(4)
        elif bad == "future":
            rows[0][0] = str(now + duration)
        elif bad == "stale":
            for row in rows:
                row[0] = str(int(row[0]) - duration * 5)
        else:
            rows[0][4] = "NaN"
        return httpx.Response(200, json={"code": "0", "data": rows})

    with pytest.raises(ValueError, match="Public structure unavailable"):
        PublicStructureSource(httpx.Client(transport=httpx.MockTransport(handler))).fetch(
            "BTC/USDT", now_ms=now
        )


@pytest.mark.asyncio
async def test_service_isolates_assets_and_reads_do_not_poll(tmp_path):
    class Source:
        closed = False

        def fetch(self, symbol, *, now_ms):
            if symbol == "ETH/USDT":
                raise ValueError("private upstream details")
            return frame(timestamp_ms=now_ms)

        def close(self):
            self.closed = True

    source = Source()

    def evidence(symbol, now):
        return {
            "generated_at_ms": now,
            "quality": {"status": "insufficient"},
            "spot": {"venues": []},
            "derivatives": {"venues": []},
        }

    with Journal(tmp_path / "journal.sqlite") as journal:
        service = WatchService(WatchEngine(journal), source, evidence, clock=lambda: NOW)
        await service.poll_once()
        state = service.snapshot()
        assert state["assets"]["BTC/USDT"]["status"] == "ok"
        assert state["assets"]["ETH/USDT"]["error"] == "structure_unavailable"
        assert state["assets"]["SOL/USDT"]["status"] == "ok"
        assert len(journal.candidates()) == 8
        assert service.snapshot() == state
        await service.start()
        await service.stop()
    assert source.closed


@pytest.mark.asyncio
async def test_snapshot_marks_cached_candidates_stale(tmp_path):
    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame(timestamp_ms=now_ms)

        def close(self):
            pass

    clock = [NOW]
    with Journal(tmp_path / "journal.sqlite") as journal:
        service = WatchService(
            WatchEngine(journal), Source(), lambda symbol, now: {}, clock=lambda: clock[0]
        )
        await service.poll_once()
        clock[0] += 60_001
        snapshot = service.snapshot()
        assert snapshot["assets"]["BTC/USDT"]["status"] == "stale"
        assert snapshot["assets"]["BTC/USDT"]["candidates"] == []


@pytest.mark.asyncio
async def test_signal_watch_api_only_reads_state(monkeypatch):
    from importlib import import_module

    api = import_module("quant_trading_platform.api.app")

    class Service:
        def snapshot(self):
            return {"assets": {}, "paper_only": True, "live_execution": False}

    monkeypatch.setattr(api.app.state, "signal_watch", Service(), raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("GET attempted upstream I/O")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        result = await client.get("/crypto-signal-watch")
        assert result.status_code == 200
        assert result.json()["paper_only"] is True
        assert (await client.post("/crypto-signal-watch")).status_code == 405


@pytest.mark.asyncio
async def test_complete_candles_and_flow_generate_paper_candidate_without_orders(tmp_path):
    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame(timestamp_ms=now_ms)

        def close(self):
            pass

    def evidence(symbol, now):
        return {
            "generated_at_ms": now,
            "quality": {"status": "healthy"},
            "spot": {
                "venues": [
                    {"venue": "binance", "depth_imbalance": "0.8"},
                    {"venue": "bybit", "depth_imbalance": "0.8"},
                ]
            },
        }

    with Journal(tmp_path / "journal.sqlite") as journal:
        service = WatchService(WatchEngine(journal), Source(), evidence, clock=lambda: NOW)
        await service.poll_once()
        rows = service.snapshot()["assets"]["BTC/USDT"]["candidates"]
        momentum = next(row for row in rows if row["setup"] == "Momentum")
        assert momentum["score"] == "81.0"
        assert momentum["confidence"] == "HIGH"
        assert momentum["paper_only"] and not momentum["live_execution"]
        assert len(journal.candidates()) == 12


@pytest.mark.asyncio
async def test_external_provider_failure_does_not_destroy_healthy_structure(tmp_path):
    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame(timestamp_ms=now_ms)

        def close(self):
            pass

    def unavailable(symbol):
        raise ValueError("provider token should not leak")

    with Journal(tmp_path / "journal.sqlite") as journal:
        service = WatchService(
            WatchEngine(journal),
            Source(),
            lambda symbol, now: {},
            external=unavailable,
            clock=lambda: NOW,
        )
        await service.poll_once()
        state = service.snapshot()["assets"]["BTC/USDT"]
        assert state["status"] == "ok"
        assert state["warnings"] == ["external_evidence_unavailable"]
        assert len(state["candidates"]) == 4


@pytest.mark.asyncio
async def test_service_registers_and_measures_price_markouts(tmp_path):
    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame(
                timestamp_ms=now_ms, high=D("111"), close=D("110") if now_ms > NOW else D("102")
            )

        def close(self):
            pass

    clock = [NOW]
    with Journal(tmp_path / "journal.sqlite") as journal:
        service = WatchService(
            WatchEngine(journal), Source(), lambda symbol, now: {}, clock=lambda: clock[0]
        )
        await service.poll_once()
        clock[0] += 900_000
        await service.poll_once()
        assert service.outcomes.calibration()["REJECTED"]["samples"] > 0
        state = service.snapshot()
        assert state["outcomes"]["kind"] == "estimated_forward_markout"


@pytest.mark.asyncio
async def test_watch_api_exposes_cached_provider_quality_without_polling(monkeypatch):
    from importlib import import_module

    from quant_trading_platform.signal_watch.providers import ProviderEvidenceCache

    api = import_module("quant_trading_platform.api.app")

    class Service:
        def snapshot(self):
            return {"assets": {}, "paper_only": True, "live_execution": False}

    cache = ProviderEvidenceCache()
    monkeypatch.setattr(api, "now_ms", lambda: NOW)
    monkeypatch.setattr(api.app.state, "signal_watch", Service(), raising=False)
    monkeypatch.setattr(api.app.state, "signal_watch_providers", cache, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        result = (await client.get("/crypto-signal-watch")).json()
    assert len(result["providers"]) == 18
    assert all(r["status"] == "no_data" for r in result["providers"])


@pytest.mark.asyncio
async def test_snapshot_is_safe_in_fastapi_worker_thread(tmp_path):
    import asyncio

    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame(timestamp_ms=now_ms)

        def close(self):
            pass

    with Journal(tmp_path / "journal.sqlite") as journal:
        service = WatchService(
            WatchEngine(journal), Source(), lambda symbol, now: {}, clock=lambda: NOW
        )
        await service.poll_once()
        state = await asyncio.to_thread(service.snapshot)
        assert state["paper_only"] is True
        assert state["outcomes"]["kind"] == "estimated_forward_markout"
