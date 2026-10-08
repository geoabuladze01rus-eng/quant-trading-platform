import asyncio

import pytest

from quant_trading_platform.signal_watch.collection import ProviderCollector
from quant_trading_platform.signal_watch.providers import ProviderEvidenceCache

NOW = 1_000_000


def seed(cache, source):
    assert cache.update(
        source,
        {
            "symbol": "BTC/USDT",
            "observations": [
                {"domain": "A", "strength": "1", "timestamp_ms": NOW, "origin": source + "_market"}
            ],
        },
        now_ms=NOW,
    )


@pytest.mark.asyncio
async def test_failed_refresh_clears_only_its_source_and_retains_healthy_confirmation():
    cache = ProviderEvidenceCache()
    for source in ("TraderSpy", "Gina", "CryptoAudit"):
        seed(cache, source)

    async def fail(symbol):
        raise RuntimeError("private upstream detail")

    async def healthy(symbol):
        return True

    collector = ProviderCollector(cache, {"TraderSpy": fail, "Gina": healthy}, clock=lambda: NOW)
    assert await collector.collect("BTC/USDT")
    assert {o.source for o in cache.observations("BTC/USDT", now_ms=NOW)} == {"Gina", "CryptoAudit"}
    assert collector.status["TraderSpy", "BTC/USDT"] == "provider_collection_unavailable"
    assert cache.status("TraderSpy", "BTC/USDT", now_ms=NOW) == "error"


@pytest.mark.asyncio
async def test_timeout_clears_hanging_source_without_cancelling_healthy_source():
    cache = ProviderEvidenceCache()
    seed(cache, "TraderSpy")

    async def hang(symbol):
        await asyncio.Event().wait()

    async def healthy(symbol):
        seed(cache, "Gina")
        return True

    collector = ProviderCollector(
        cache, {"TraderSpy": hang, "Gina": healthy}, clock=lambda: NOW, timeout_seconds=0.01
    )
    assert await collector.collect("BTC/USDT")
    assert [o.source for o in cache.observations("BTC/USDT", now_ms=NOW)] == ["Gina"]


@pytest.mark.asyncio
async def test_orchestration_cancellation_invalidates_inflight_source():
    cache = ProviderEvidenceCache()
    seed(cache, "TraderSpy")
    entered = asyncio.Event()

    async def hang(symbol):
        entered.set()
        await asyncio.Event().wait()

    collector = ProviderCollector(cache, {"TraderSpy": hang}, clock=lambda: NOW)
    task = asyncio.create_task(collector.collect("BTC/USDT"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not cache.observations("BTC/USDT", now_ms=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [False, None, "ok", 1])
async def test_non_boolean_or_failed_refresh_does_not_keep_previous_source(result):
    cache = ProviderEvidenceCache()
    seed(cache, "Gina")

    async def refresh(symbol):
        return result

    collector = ProviderCollector(cache, {"Gina": refresh}, clock=lambda: NOW)
    await collector.collect("BTC/USDT")
    assert not cache.observations("BTC/USDT", now_ms=NOW)


def test_collector_rejects_unknown_provider_and_unbounded_deadline():
    cache = ProviderEvidenceCache()
    with pytest.raises(ValueError):
        ProviderCollector(cache, {"invented": lambda symbol: None})
    with pytest.raises(ValueError):
        ProviderCollector(cache, {}, timeout_seconds=10)
