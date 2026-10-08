import asyncio

import pytest

from quant_trading_platform.market_data.spot_signal_service import SpotSignalService


class BrokenSource:
    calls = 0
    closed = False

    def fetch(self, symbol: str, *, now_ms: int) -> tuple:
        self.calls += 1
        raise RuntimeError("secret must not escape")

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_unexpected_refresh_failure_invalidates_snapshot_and_retries() -> None:
    source = BrokenSource()
    service = SpotSignalService(source, interval_seconds=0.01)  # type: ignore[arg-type]
    await service.start()
    try:
        async with asyncio.timeout(1):
            while source.calls < 6:
                await asyncio.sleep(0.01)
        assert service.snapshot()["status"] == "unavailable"
        assert service.snapshot()["signals"] == []
        assert "secret" not in str(service.snapshot())
    finally:
        await service.stop()
    assert source.closed


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_releases_single_worker() -> None:
    source = BrokenSource()
    service = SpotSignalService(source)  # type: ignore[arg-type]
    await service.start()
    first = service._task
    await service.start()
    second = service._task
    try:
        await service.stop()
        assert second is first
        assert first is not None and first.done()
        assert service._task is None
    finally:
        if first is not None and not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
