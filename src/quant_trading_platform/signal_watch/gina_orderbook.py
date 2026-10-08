"""Public Gina canonical Hyperliquid flow, without table creation or private data."""

import asyncio
import json
from collections.abc import Callable
from decimal import Decimal
from time import time_ns

from quant_trading_platform.market_data.derivatives import SIGNAL_SYMBOLS, exact_decimal
from quant_trading_platform.models import normalize_symbol
from quant_trading_platform.signal_watch.providers import ProviderEvidenceCache
from quant_trading_platform.signal_watch.traderspy import ReadOnlyToolExecutor


def adapt_gina_order_book(
    symbol: str, raw_json: str, *, now_ms: int, depth: int = 5
) -> dict[str, object]:
    symbol = normalize_symbol(symbol)
    if (
        symbol not in SIGNAL_SYMBOLS
        or type(now_ms) is not int
        or now_ms <= 0
        or type(depth) is not int
        or not 1 <= depth <= 10
        or len(raw_json) > 1_048_576
    ):
        raise ValueError("Invalid bounded book request")
    rows = json.loads(raw_json, parse_float=Decimal)
    if not isinstance(rows, list) or len(rows) != 2 * depth:
        raise ValueError("Incomplete book snapshot")
    sides: dict[str, dict[int, tuple[Decimal, Decimal]]] = {"bid": {}, "ask": {}}
    timestamp: int | None = None
    mid: Decimal | None = None
    for row in rows:
        if not isinstance(row, dict) or row.get("coin") != symbol.split("/")[0]:
            raise ValueError("Wrong book identity")
        side, level, count, clock = (
            row.get(key) for key in ("side", "level", "count", "timestamp")
        )
        if (
            not isinstance(side, str)
            or side not in sides
            or type(level) is not int
            or not 1 <= level <= depth
            or level in sides[side]
            or type(count) is not int
            or count <= 0
            or type(clock) is not int
            or not 0 <= now_ms - clock <= 5_000
        ):
            raise ValueError("Unverified book level/freshness")
        price, size, midpoint = (
            exact_decimal(row.get(key)) for key in ("price", "size", "midPrice")
        )
        if price is None or size is None or midpoint is None:
            raise ValueError("Unverified book measurements")
        if (timestamp is not None and clock != timestamp) or (mid is not None and midpoint != mid):
            raise ValueError("Mixed source snapshot")
        timestamp, mid = clock, midpoint
        sides[side][level] = (price, size)
    for side, levels in sides.items():
        if set(levels) != set(range(1, depth + 1)):
            raise ValueError("Incomplete side depth")
        for level in range(2, depth + 1):
            previous, current = levels[level - 1][0], levels[level][0]
            if (side == "bid" and current >= previous) or (side == "ask" and current <= previous):
                raise ValueError("Invalid book ordering")
    bid, ask = sides["bid"][1][0], sides["ask"][1][0]
    if bid >= ask or mid != (bid + ask) / 2:
        raise ValueError("Crossed or inconsistent book")
    notionals = {
        side: sum((price * size for price, size in levels.values()), Decimal(0))
        for side, levels in sides.items()
    }
    strength = max(
        Decimal(0), (notionals["bid"] - notionals["ask"]) / (notionals["bid"] + notionals["ask"])
    )
    return {
        "symbol": symbol,
        "observations": [
            {
                "domain": "D",
                "strength": str(strength),
                "timestamp_ms": timestamp,
                "origin": "hyperliquid_canonical_usdc_depth",
            }
        ],
    }


def _original_array(result: object) -> str:
    if not isinstance(result, dict) or result.get("isError"):
        raise ValueError("Unavailable public tool response")
    content = result.get("content")
    if not isinstance(content, list):
        raise ValueError("Original source JSON unavailable")
    arrays = [
        row["text"]
        for row in content
        if isinstance(row, dict)
        and row.get("type") == "text"
        and isinstance(row.get("text"), str)
        and row["text"].lstrip().startswith("[")
    ]
    if len(arrays) != 1 or not isinstance(arrays[0], str):
        raise ValueError("Ambiguous original source JSON")
    return arrays[0]


class GinaOrderBookProducer:
    def __init__(
        self,
        cache: ProviderEvidenceCache,
        execute: ReadOnlyToolExecutor,
        *,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
        depth: int = 5,
        timeout_seconds: float = 5,
    ) -> None:
        if type(depth) is not int or not 1 <= depth <= 10 or not 0 < timeout_seconds <= 5:
            raise ValueError("Invalid bounded public book collection")
        self.cache, self.execute, self.clock = cache, execute, clock
        self.depth, self.timeout_seconds = depth, timeout_seconds
        self.status: dict[str, str] = {}

    def _invalidate(self, symbol: str) -> None:
        self.cache.update("Gina", {"symbol": symbol, "observations": []}, now_ms=self.clock())
        self.status[symbol] = "provider_evidence_unavailable"

    async def collect(self, symbol: str) -> bool:
        symbol = normalize_symbol(symbol)
        if symbol not in SIGNAL_SYMBOLS:
            raise ValueError("Unsupported public book symbol")
        try:
            result = await asyncio.wait_for(
                self.execute(
                    "gina_fetch_hyperliquid_orderbook",
                    {"coin": symbol.split("/")[0], "depth": self.depth},
                ),
                self.timeout_seconds,
            )
            now = self.clock()
            report = adapt_gina_order_book(
                symbol, _original_array(result), now_ms=now, depth=self.depth
            )
            if not self.cache.update("Gina", report, now_ms=now):
                raise ValueError("Rejected public book evidence")
            self.status[symbol] = "ok"
            return True
        except asyncio.CancelledError:
            self._invalidate(symbol)
            raise
        except Exception:
            self._invalidate(symbol)
            return False
