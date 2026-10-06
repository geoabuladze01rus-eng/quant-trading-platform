import asyncio
from dataclasses import replace

import pytest

from quant_trading_platform.config import Settings
from quant_trading_platform.market_data.derivatives import (
    DerivativesEvidenceService,
    MultiDerivativesEvidenceService,
    normalize_derivatives_snapshot,
)
from quant_trading_platform.models import Venue


class Source:
    def __init__(self, venue, fail=False):
        self.venue, self.fail, self.closed, self.calls = venue, fail, False, 0
        self.value = normalize_derivatives_snapshot(
            venue=venue,
            symbol="BTC/USDT",
            instrument_id="BTC-USDT-SWAP" if venue == Venue.OKX else "BTCUSDT",
            timestamp_ms=1000,
            received_at_ms=1000,
            open_interest="2",
            open_interest_unit="contracts" if venue == Venue.OKX else "base_asset",
        )

    def get_snapshot(self, symbol):
        self.calls += 1
        if self.fail:
            raise RuntimeError("secret")
        return self.value

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_failure_isolated_and_reads_never_poll_or_expose_last_good():
    sources = [Source(Venue.BINANCE), Source(Venue.BYBIT), Source(Venue.OKX, True)]
    now = [1000]
    service = DerivativesEvidenceService(sources, clock=lambda: now[0], max_age_ms=100)
    await service.poll_once()
    assert service.fresh_snapshot(Venue.BINANCE, "BTC/USDT") is not None
    assert service.fresh_snapshot(Venue.OKX, "BTC/USDT") is None
    assert service.snapshot()[2]["error"] == "public_derivatives_unavailable"
    sources[0].fail = True
    await service.poll_once()
    assert service.fresh_snapshot(Venue.BINANCE, "BTC/USDT") is None
    before = [s.calls for s in sources]
    now[0] = 1101
    assert service.fresh_snapshot(Venue.BYBIT, "BTC/USDT") is None
    assert service.snapshot()[1]["status"] == "stale"
    assert [s.calls for s in sources] == before
    await service.stop()
    assert all(s.closed for s in sources)


@pytest.mark.asyncio
async def test_future_and_wrong_identity_fail_closed_and_previous_only_comparable():
    source = Source(Venue.BINANCE)
    service = DerivativesEvidenceService([source], clock=lambda: 1010)
    await service.poll_once()
    old = source.value
    source.value = replace(
        old, open_interest=old.open_interest * 2, timestamp_ms=1001, received_at_ms=1001
    )
    await service.poll_once()
    assert service.states[Venue.BINANCE].previous == old
    source.value = replace(
        source.value, open_interest_unit="contracts", timestamp_ms=1002, received_at_ms=1002
    )
    await service.poll_once()
    assert service.states[Venue.BINANCE].previous is None
    source.value = replace(source.value, timestamp_ms=2000)
    await service.poll_once()
    assert service.fresh_snapshot(Venue.BINANCE, "BTC/USDT") is None
    source.value = replace(old, symbol="ETH/USDT")
    await service.poll_once()
    assert service.fresh_snapshot(Venue.BINANCE, "BTC/USDT") is None


@pytest.mark.asyncio
async def test_slow_source_does_not_delay_healthy_source():
    import threading

    release = threading.Event()
    slow = Source(Venue.BINANCE)
    original = slow.get_snapshot

    def blocked(symbol):
        release.wait(2)
        return original(symbol)

    slow.get_snapshot = blocked
    service = DerivativesEvidenceService([slow, Source(Venue.BYBIT)], clock=lambda: 1000)
    task = asyncio.create_task(service.poll_once())
    try:
        for _ in range(100):
            if service.fresh_snapshot(Venue.BYBIT, "BTC/USDT"):
                break
            await asyncio.sleep(0.005)
        assert service.fresh_snapshot(Venue.BYBIT, "BTC/USDT") is not None
        assert not task.done()
    finally:
        release.set()
        await task
        await service.stop()


def test_polling_config_keeps_ltc_and_adds_sol():
    settings = Settings()
    assert "LTC/USDT" in settings.market_data_symbols
    assert "SOL/USDT" in settings.market_data_symbols
    assert settings.derivatives_data_enabled
    assert settings.derivatives_poll_interval_seconds >= 5
    with pytest.raises(ValueError):
        Settings(derivatives_poll_interval_seconds=0.1)
    with pytest.raises(ValueError):
        MultiDerivativesEvidenceService([])
