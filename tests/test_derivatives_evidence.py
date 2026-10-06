import asyncio
from dataclasses import replace
from decimal import Decimal
from threading import Event

import pytest

from quant_trading_platform.config import Settings
from quant_trading_platform.market_data.derivatives import (
    DerivativesEvidenceService,
    DerivativesSnapshot,
    MultiDerivativesEvidenceService,
    normalize_derivatives_snapshot,
)
from quant_trading_platform.models import Venue


class Source:
    def __init__(self, venue: Venue, *, oi: str = "100", unit: str = "BTC") -> None:
        self.venue = venue
        self.oi = oi
        self.unit = unit
        self.error = False
        self.closed = False
        self.calls = 0
        self.timestamp_ms = 10_000

    def get_snapshot(self, symbol: str) -> DerivativesSnapshot:
        self.calls += 1
        if self.error:
            raise OSError("upstream secret")
        instrument = (
            symbol.replace("/", "")
            if self.venue in (Venue.BINANCE, Venue.BYBIT)
            else f"{symbol.replace('/', '-')}-SWAP"
        )
        return normalize_derivatives_snapshot(
            venue=self.venue,
            symbol=symbol,
            instrument_id=instrument,
            timestamp_ms=self.timestamp_ms,
            received_at_ms=self.timestamp_ms,
            mark_price="100",
            index_price="99.5",
            funding_rate="0.0001",
            next_funding_time_ms=20_000,
            open_interest=self.oi,
            open_interest_unit=self.unit,
            source_fields=("mark_price", "index_price", "funding_rate", "open_interest"),
            max_age_ms=10_000,
        )

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_poll_isolates_failure_and_never_returns_failed_last_good_snapshot() -> None:
    binance = Source(Venue.BINANCE)
    bybit = Source(Venue.BYBIT)
    service = DerivativesEvidenceService(
        [binance, bybit],
        symbol="BTC/USDT",
        clock=lambda: 10_000,
        max_age_ms=1_000,
    )
    await service.poll_once()
    assert service.fresh_snapshot(Venue.BINANCE, "BTC/USDT") is not None
    assert service.fresh_snapshot(Venue.BYBIT, "BTC/USDT") is not None

    binance.error = True
    await service.poll_once()

    assert service.fresh_snapshot(Venue.BINANCE, "BTC/USDT") is None
    assert service.fresh_snapshot(Venue.BYBIT, "BTC/USDT") is not None
    rows = service.snapshot()
    assert rows[0]["status"] == "error"
    assert rows[0]["error"] == "public_derivatives_data_unavailable"
    assert "secret" not in str(rows)


@pytest.mark.asyncio
async def test_read_methods_have_no_polling_side_effects_and_age_last_good_data() -> None:
    now = 10_000
    source = Source(Venue.OKX, unit="contracts")
    service = DerivativesEvidenceService(
        [source],
        symbol="BTC/USDT",
        clock=lambda: now,
        max_age_ms=1_000,
    )
    assert service.snapshot()[0]["status"] == "no_data"
    await service.poll_once()
    assert source.calls == 1
    assert service.fresh_snapshot(Venue.OKX, "BTC-USDT") is not None

    now = 12_000

    assert service.fresh_snapshot(Venue.OKX, "BTC/USDT") is None
    assert service.snapshot()[0]["status"] == "stale"
    assert source.calls == 1


@pytest.mark.asyncio
async def test_future_snapshot_is_never_healthy() -> None:
    source = Source(Venue.BINANCE)
    source.timestamp_ms = 10_001
    service = DerivativesEvidenceService(
        [source],
        symbol="BTC/USDT",
        clock=lambda: 10_000,
        max_age_ms=1_000,
    )
    await service.poll_once()
    assert service.fresh_snapshot(Venue.BINANCE, "BTC/USDT") is None
    assert service.snapshot()[0]["status"] == "error"


@pytest.mark.asyncio
async def test_previous_open_interest_is_kept_only_when_unit_and_instrument_match() -> None:
    source = Source(Venue.BINANCE, oi="100", unit="BTC")
    service = DerivativesEvidenceService(
        [source],
        symbol="BTC/USDT",
        clock=lambda: 10_000,
        max_age_ms=1_000,
    )
    await service.poll_once()
    source.oi = "110"
    await service.poll_once()
    state = service.states[Venue.BINANCE]
    assert state.previous_snapshot is not None
    assert state.previous_snapshot.open_interest == Decimal("100")
    assert state.snapshot is not None
    assert state.snapshot.open_interest == Decimal("110")

    state.snapshot = replace(state.snapshot, open_interest_unit="contracts")
    source.oi = "120"
    await service.poll_once()
    assert service.states[Venue.BINANCE].previous_snapshot is None


@pytest.mark.asyncio
async def test_multiple_symbols_are_independent() -> None:
    btc = DerivativesEvidenceService(
        [Source(Venue.BINANCE)],
        symbol="BTC/USDT",
        clock=lambda: 10_000,
    )
    eth = DerivativesEvidenceService(
        [Source(Venue.BINANCE, unit="ETH")],
        symbol="ETH/USDT",
        clock=lambda: 10_000,
    )
    service = MultiDerivativesEvidenceService([btc, eth])
    await asyncio.gather(btc.poll_once(), eth.poll_once())
    assert service.fresh_snapshot(Venue.BINANCE, "BTC/USDT") is not None
    assert service.fresh_snapshot(Venue.BINANCE, "ETHUSDT") is not None
    assert service.fresh_snapshot(Venue.BINANCE, "SOL/USDT") is None


@pytest.mark.asyncio
async def test_slow_venue_does_not_delay_healthy_venue_polling() -> None:
    release = Event()

    class SlowSource(Source):
        def get_snapshot(self, symbol: str) -> DerivativesSnapshot:
            release.wait(timeout=2)
            return super().get_snapshot(symbol)

    slow = SlowSource(Venue.BINANCE)
    fast = Source(Venue.BYBIT)
    service = DerivativesEvidenceService(
        [slow, fast],
        symbol="BTC/USDT",
        interval_seconds=0.25,
        clock=lambda: 10_000,
    )
    await service.start()
    try:
        async with asyncio.timeout(1.5):
            while fast.calls < 2:
                await asyncio.sleep(0.01)
        assert slow.calls == 0
        assert service.snapshot()[1]["status"] == "ok"
    finally:
        release.set()
        await service.stop()


@pytest.mark.asyncio
async def test_stop_closes_sources_without_leaking_background_task() -> None:
    source = Source(Venue.BINANCE)
    service = DerivativesEvidenceService(
        [source],
        symbol="BTC/USDT",
        interval_seconds=0.25,
        clock=lambda: 10_000,
    )
    before = set(asyncio.all_tasks())
    await service.start()
    await asyncio.sleep(0.05)
    await service.stop()
    assert source.closed
    assert source.calls >= 1
    assert not (set(asyncio.all_tasks()) - before)


def test_settings_add_sol_without_removing_existing_ltc_paper_feed() -> None:
    settings = Settings(_env_file=None)
    assert settings.market_data_symbols == "BTC/USDT,ETH/USDT,LTC/USDT,SOL/USDT"
    assert settings.derivatives_data_enabled is True
    assert 0.25 <= settings.derivatives_poll_interval_seconds <= 60
    assert settings.max_derivatives_data_age_ms > 0
