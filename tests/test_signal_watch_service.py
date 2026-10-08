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
                    {
                        "venue": "binance",
                        "depth_imbalance": "0.8",
                        "timestamp_ms": now,
                        "received_at_ms": now,
                    },
                    {
                        "venue": "bybit",
                        "depth_imbalance": "0.8",
                        "timestamp_ms": now,
                        "received_at_ms": now,
                    },
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


@pytest.mark.asyncio
async def test_authorized_external_collector_populates_cache_before_scoring(tmp_path):
    from quant_trading_platform.signal_watch.providers import ProviderEvidenceCache

    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame(timestamp_ms=now_ms)

        def close(self):
            pass

    cache = ProviderEvidenceCache()
    calls = []

    async def collect(symbol):
        calls.append(symbol)
        return cache.update(
            "TraderSpy",
            {
                "symbol": symbol,
                "observations": [
                    {
                        "domain": "A",
                        "strength": "1",
                        "timestamp_ms": NOW,
                        "origin": "binance_usdm_candles",
                    }
                ],
            },
            now_ms=NOW,
        )

    with Journal(tmp_path / "journal.sqlite") as journal:
        service = WatchService(
            WatchEngine(journal),
            Source(),
            lambda symbol, now: {},
            external=lambda symbol: cache.observations(symbol, now_ms=NOW),
            collect_external=collect,
            clock=lambda: NOW,
        )
        await service.poll_once()
        assert set(calls) == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
        rows = service.snapshot()["assets"]["BTC/USDT"]["candidates"]
        assert any("TraderSpy" in row["sources"] for row in rows)


@pytest.mark.asyncio
async def test_failed_external_collector_keeps_asset_diagnostics_available(tmp_path):
    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame(timestamp_ms=now_ms)

        def close(self):
            pass

    async def collect(symbol):
        raise RuntimeError("private upstream detail")

    with Journal(tmp_path / "journal.sqlite") as journal:
        service = WatchService(
            WatchEngine(journal),
            Source(),
            lambda symbol, now: {},
            collect_external=collect,
            clock=lambda: NOW,
        )
        await service.poll_once()
        state = service.snapshot()["assets"]["BTC/USDT"]
        assert state["status"] == "ok"
        assert "external_collection_unavailable" in state["warnings"]


@pytest.mark.asyncio
async def test_failed_refresh_excludes_previously_cached_external_evidence(tmp_path):
    from quant_trading_platform.signal_watch.intelligence import Observation

    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame()

    async def collect(symbol):
        return False

    journal = Journal(tmp_path / "watch.sqlite3")
    service = WatchService(
        WatchEngine(journal),
        Source(),
        lambda symbol, now: {"quality": {"status": "healthy"}},
        external=lambda symbol: [Observation("TraderSpy", "A", D(1), NOW, "binance")],
        collect_external=collect,
        clock=lambda: NOW,
    )
    await service.poll_once()
    assert all(
        "TraderSpy" not in candidate["sources"]
        for state in service.assets.values()
        for candidate in state["candidates"]
    )
    journal.close()


@pytest.mark.asyncio
async def test_lifespan_binds_explicit_native_executor(tmp_path, monkeypatch):
    from importlib import import_module

    from quant_trading_platform.config import Settings
    from quant_trading_platform.signal_watch.traderspy import TraderSpyProducer

    api = import_module("quant_trading_platform.api.app")

    async def idle(self):
        pass

    async def execute(tool, arguments):
        raise AssertionError("Startup must not call hosted tools in this test")

    for cls in (
        api.MultiMarketDataService,
        api.SpotSignalService,
        api.MultiDerivativesEvidenceService,
        api.PublicLiquidationCollector,
        api.AutomaticSpotPaper,
        api.WatchService,
    ):
        monkeypatch.setattr(cls, "start", idle)
    monkeypatch.setattr(
        api, "settings", Settings(_env_file=None, paper_database_path=tmp_path / "paper.db")
    )
    monkeypatch.setattr(api.app.state, "read_only_tool_executor", execute, raising=False)
    async with api.lifespan(api.app):
        assert isinstance(api.app.state.signal_watch_traderspy, TraderSpyProducer)
        assert (
            api.app.state.signal_watch.collect_external
            == api.app.state.signal_watch_collector.collect
        )
        assert (
            api.app.state.signal_watch_collector.refreshers["TraderSpy"]
            == api.app.state.signal_watch_traderspy.collect
        )
        assert api.app.state.signal_watch_traderspy.cache is api.app.state.signal_watch_providers


@pytest.mark.asyncio
async def test_lifespan_isolates_registered_provider_refreshers(tmp_path, monkeypatch):
    from importlib import import_module

    from quant_trading_platform.config import Settings
    from quant_trading_platform.signal_watch.collection import ProviderCollector

    api = import_module("quant_trading_platform.api.app")

    async def idle(self):
        pass

    async def refresh(symbol):
        return api.app.state.signal_watch_providers.update(
            "Gina",
            {
                "symbol": symbol,
                "observations": [
                    {
                        "domain": "A",
                        "strength": "1",
                        "timestamp_ms": api.now_ms(),
                        "origin": "hyperliquid_canonical_usdc_candles",
                    }
                ],
            },
            now_ms=api.now_ms(),
        )

    for cls in (
        api.MultiMarketDataService,
        api.SpotSignalService,
        api.MultiDerivativesEvidenceService,
        api.PublicLiquidationCollector,
        api.AutomaticSpotPaper,
        api.WatchService,
    ):
        monkeypatch.setattr(cls, "start", idle)
    monkeypatch.setattr(
        api, "settings", Settings(_env_file=None, paper_database_path=tmp_path / "paper.db")
    )
    monkeypatch.setattr(api.app.state, "read_only_tool_executor", None, raising=False)
    monkeypatch.setattr(
        api.app.state, "signal_watch_external_refreshers", {"Gina": refresh}, raising=False
    )
    async with api.lifespan(api.app):
        assert isinstance(api.app.state.signal_watch_collector, ProviderCollector)
        assert await api.app.state.signal_watch.collect_external("BTC/USDT")
        assert (
            api.app.state.signal_watch_providers.status("Gina", "BTC/USDT", now_ms=api.now_ms())
            == "ok"
        )


@pytest.mark.asyncio
async def test_native_executor_binds_gina_book_without_remote_table_creation(tmp_path, monkeypatch):
    from importlib import import_module

    from quant_trading_platform.config import Settings
    from quant_trading_platform.signal_watch.gina_orderbook import GinaOrderBookProducer

    api = import_module("quant_trading_platform.api.app")

    async def idle(self):
        pass

    async def execute(tool, arguments):
        raise AssertionError("No hosted calls during mocked startup")

    for cls in (
        api.MultiMarketDataService,
        api.SpotSignalService,
        api.MultiDerivativesEvidenceService,
        api.PublicLiquidationCollector,
        api.AutomaticSpotPaper,
        api.WatchService,
    ):
        monkeypatch.setattr(cls, "start", idle)
    monkeypatch.setattr(
        api, "settings", Settings(_env_file=None, paper_database_path=tmp_path / "paper.db")
    )
    monkeypatch.setattr(api.app.state, "signal_watch_external_refreshers", {}, raising=False)
    monkeypatch.setattr(api.app.state, "read_only_tool_executor", execute, raising=False)
    async with api.lifespan(api.app):
        producer = api.app.state.signal_watch_gina
        assert isinstance(producer, GinaOrderBookProducer)
        assert api.app.state.signal_watch_collector.refreshers["Gina"] == producer.collect
        assert producer.cache is api.app.state.signal_watch_providers


@pytest.mark.asyncio
@pytest.mark.parametrize("source_age,expiry", [(0, 5_000), (4_000, 1_000)])
async def test_cached_candidate_expires_with_its_contributing_gina_book(
    tmp_path, source_age, expiry
):
    from quant_trading_platform.signal_watch.intelligence import Observation

    current = NOW

    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame()

    journal = Journal(tmp_path / "watch.db")
    service = WatchService(
        WatchEngine(journal),
        Source(),
        lambda symbol, now: {"quality": {"status": "healthy"}},
        external=lambda symbol: [
            Observation("Gina", "D", D("0.9"), NOW - source_age, "hyperliquid_canonical_usdc_depth")
        ],
        clock=lambda: current,
    )
    await service.poll_once()
    state = service.snapshot()["assets"]["BTC/USDT"]
    assert any(c["confidence"] == "HIGH" for c in state["candidates"])
    recorded = journal.connection.total_changes
    current += expiry
    assert service.snapshot()["assets"]["BTC/USDT"]["status"] == "ok"
    current += 1
    expired = service.snapshot()["assets"]["BTC/USDT"]
    assert expired["status"] == "ok"
    assert expired["candidate_status"] == "evidence_expired"
    assert expired["candidates"] == []
    assert journal.connection.total_changes == recorded
    assert service.assets["BTC/USDT"]["candidates"]  # GET did not mutate cached records.
    journal.close()


@pytest.mark.asyncio
async def test_data_hub_flow_preserves_source_clock_and_cached_source_expiry(tmp_path):
    current = NOW

    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame()

    def evidence(symbol, now):
        return {
            "generated_at_ms": now,
            "quality": {"status": "healthy"},
            "spot": {
                "venues": [
                    {
                        "venue": "binance",
                        "depth_imbalance": "0.8",
                        "timestamp_ms": NOW - 400,
                        "received_at_ms": NOW - 300,
                    },
                    {
                        "venue": "bybit",
                        "depth_imbalance": "0.8",
                        "timestamp_ms": NOW - 200,
                        "received_at_ms": NOW - 100,
                    },
                ]
            },
        }

    journal = Journal(tmp_path / "watch.db")
    service = WatchService(WatchEngine(journal), Source(), evidence, clock=lambda: current)
    await service.poll_once()
    state = service.snapshot()["assets"]["BTC/USDT"]
    accepted = next(c for c in state["candidates"] if c["confidence"] == "HIGH")
    observation = next(o for o in accepted["evidence"] if o["source"] == "Data Hub")
    assert observation["timestamp_ms"] == NOW - 400
    current += 601
    assert service.snapshot()["assets"]["BTC/USDT"]["status"] == "ok"
    assert service.snapshot()["assets"]["BTC/USDT"]["candidate_status"] == "evidence_expired"
    journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_clock", "future", "stale", "duplicate", "imbalance"])
async def test_invalid_data_hub_flow_does_not_become_fresh_from_response_time(tmp_path, fault):
    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame()

    rows = [
        {"venue": venue, "depth_imbalance": "0.8", "timestamp_ms": NOW, "received_at_ms": NOW}
        for venue in ("binance", "bybit")
    ]
    if fault == "missing_clock":
        del rows[0]["timestamp_ms"]
    elif fault == "future":
        rows[0]["timestamp_ms"] = NOW + 1
    elif fault == "stale":
        rows[0]["timestamp_ms"] = NOW - 1_001
    elif fault == "duplicate":
        rows[1]["venue"] = "binance"
    elif fault == "imbalance":
        rows[0]["depth_imbalance"] = "2"
    journal = Journal(tmp_path / "watch.db")
    service = WatchService(
        WatchEngine(journal),
        Source(),
        lambda symbol, now: {
            "generated_at_ms": now,
            "quality": {"status": "healthy"},
            "spot": {"venues": rows},
        },
        clock=lambda: NOW,
    )
    await service.poll_once()
    state = service.assets["BTC/USDT"]
    assert state["status"] == "ok"
    assert "flow_evidence_unavailable" in state["warnings"]
    assert all(c["domains"]["D"] == "0" for c in state["candidates"])
    journal.close()


@pytest.mark.asyncio
async def test_okx_only_depth_counts_as_one_verifiable_source_not_venue_consensus(tmp_path):
    from quant_trading_platform.signal_watch.service import _spot_flow

    observation, deadline = _spot_flow(
        [{
            "venue": "okx", "depth_imbalance": "0.8",
            "timestamp_ms": NOW - 300, "received_at_ms": NOW - 200,
        }],
        NOW, 1000,
    )
    assert observation.source == "Data Hub"
    assert observation.domain == "D"
    assert observation.origin == "okx_spot_depth"
    assert observation.timestamp_ms == NOW - 300
    assert deadline == NOW + 700

    for row in (
        {"venue": "binance", "depth_imbalance": "0.8",
         "timestamp_ms": NOW, "received_at_ms": NOW},
        {"venue": "okx", "depth_imbalance": "0.8",
         "timestamp_ms": NOW - 1001, "received_at_ms": NOW},
        {"venue": "okx", "depth_imbalance": "2",
         "timestamp_ms": NOW, "received_at_ms": NOW},
    ):
        with pytest.raises(ValueError):
            _spot_flow([row], NOW, 1000)


@pytest.mark.asyncio
async def test_okx_only_scanner_uses_actual_depth_and_preserves_expiry(tmp_path):
    from quant_trading_platform.signal_watch.service import WatchService

    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame(timestamp_ms=now_ms)

        def close(self):
            pass

    current = NOW

    def evidence(symbol, now):
        return {
            "quality": {"status": "healthy"},
            "spot": {"venues": [{
                "venue": "okx", "depth_imbalance": "0.8",
                "timestamp_ms": now - 400, "received_at_ms": now - 300,
            }]},
        }

    journal = Journal(tmp_path / "okx_watch.db")
    service = WatchService(WatchEngine(journal), Source(), evidence,
                           clock=lambda: current)
    await service.poll_once()
    candidate = next(c for c in service.assets["BTC/USDT"]["candidates"]
                     if c["setup"] == "Momentum")
    assert candidate["domains"]["D"] == "16.0"
    assert "Data Hub" in candidate["sources"]
    assert any(e["origin"] == "okx_spot_depth" for e in candidate["evidence"])
    assert service.assets["BTC/USDT"]["valid_until_ms"] == NOW + 600
    current += 601
    assert service.snapshot()["assets"]["BTC/USDT"]["status"] == "ok"
    assert service.snapshot()["assets"]["BTC/USDT"]["candidate_status"] == "evidence_expired"
    assert service.snapshot()["assets"]["BTC/USDT"]["candidates"] == []
    journal.close()
