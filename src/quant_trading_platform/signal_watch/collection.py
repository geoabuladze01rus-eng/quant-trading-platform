"""Isolated authorized producer orchestration; no discovery or tool permissions."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from time import time_ns

from quant_trading_platform.market_data.derivatives import SIGNAL_SYMBOLS
from quant_trading_platform.models import normalize_symbol
from quant_trading_platform.signal_watch.intelligence import PROVIDERS
from quant_trading_platform.signal_watch.providers import ProviderEvidenceCache

Refresh = Callable[[str], Awaitable[bool]]


class ProviderCollector:
    def __init__(
        self,
        cache: ProviderEvidenceCache,
        refreshers: Mapping[str, Refresh],
        *,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
        timeout_seconds: float = 8,
    ) -> None:
        if not 0 < timeout_seconds < 10 or any(
            source not in PROVIDERS - {"Market Structure", "Data Hub"} or not callable(refresh)
            for source, refresh in refreshers.items()
        ):
            raise ValueError("Invalid provider collection boundary")
        self.cache, self.refreshers, self.clock = cache, dict(refreshers), clock
        self.timeout_seconds = timeout_seconds
        self.status: dict[tuple[str, str], str] = {}

    def _invalidate(self, source: str, symbol: str) -> None:
        self.cache.update(source, {"symbol": symbol, "observations": []}, now_ms=self.clock())
        self.status[source, symbol] = "provider_collection_unavailable"

    async def _refresh(self, source: str, refresh: Refresh, symbol: str) -> None:
        try:
            if await asyncio.wait_for(refresh(symbol), self.timeout_seconds) is not True:
                self._invalidate(source, symbol)
                return
            self.status[source, symbol] = "ok"
        except asyncio.CancelledError:
            self._invalidate(source, symbol)
            raise
        except Exception:
            self._invalidate(source, symbol)

    async def collect(self, symbol: str) -> bool:
        symbol = normalize_symbol(symbol)
        if symbol not in SIGNAL_SYMBOLS:
            raise ValueError("Unsupported collection symbol")
        await asyncio.gather(
            *(self._refresh(source, refresh, symbol) for source, refresh in self.refreshers.items())
        )
        # True means orchestration completed, not that every provider succeeded.
        # Each failed source has already been removed; healthy/manual evidence remains.
        return True
