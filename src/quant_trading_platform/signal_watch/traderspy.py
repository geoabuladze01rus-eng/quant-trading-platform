"""Verified TraderSpy public Binance USD-M candle/indicator evidence adapter.

Use the original JSON text content block, not decoded floating-point structuredContent.
Only one measured structure contribution is produced; provider AI scores are ignored.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from time import time_ns
from typing import cast

from quant_trading_platform.market_data.derivatives import SIGNAL_SYMBOLS, exact_decimal
from quant_trading_platform.models import normalize_symbol
from quant_trading_platform.signal_watch.providers import ProviderEvidenceCache

ReadOnlyToolExecutor = Callable[[str, dict[str, object]], Awaitable[object]]


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Missing source object")
    return cast(dict[str, object], value)


def _json(raw: str) -> dict[str, object]:
    if len(raw) > 1_048_576:
        raise ValueError("Oversize source JSON")
    return _object(json.loads(raw, parse_float=Decimal))


def _number(value: object, *, positive: bool = True) -> Decimal:
    result = exact_decimal(value, positive=positive)
    if result is None:
        raise ValueError("Missing measured value")
    return result


def adapt_traderspy(
    symbol: str, candle_json: str, indicator_json: str, *, now_ms: int
) -> dict[str, object]:
    symbol = normalize_symbol(symbol)
    if symbol not in SIGNAL_SYMBOLS or type(now_ms) is not int or now_ms <= 0:
        raise ValueError("Unsupported source request")
    candles, indicators = _json(candle_json), _json(indicator_json)
    for payload in (candles, indicators):
        if payload.get("symbol") != symbol.replace("/", "") or payload.get("interval") != "1m":
            raise ValueError("TraderSpy identity/timeframe mismatch")
    raw_candles = candles.get("candles")
    if not isinstance(raw_candles, list) or not 2 <= len(raw_candles) <= 50:
        raise ValueError("Insufficient bounded candle evidence")
    final: list[tuple[int, int, Decimal]] = []
    for raw in raw_candles:
        row = _object(raw)
        if row.get("isFinal") is not True:
            if row.get("isFinal") is not False:
                raise ValueError("Missing completion state")
            continue
        opening, closing = row.get("openTime"), row.get("closeTime")
        if (
            type(opening) is not int
            or type(closing) is not int
            or opening <= 0
            or opening % 60_000 != 0
            or closing != opening + 59_999
            or closing + 1 > now_ms
            or (final and opening != final[-1][0] + 60_000)
        ):
            raise ValueError("Invalid completed candle clock/continuity")
        o, h, low, close = (_number(row.get(key)) for key in ("open", "high", "low", "close"))
        volume = _number(row.get("volume"), positive=False)
        if low > min(o, close) or h < max(o, close) or volume < 0:
            raise ValueError("Invalid completed candle measurements")
        final.append((opening, closing + 1, close))
    if len(final) < 2:
        raise ValueError("Insufficient completed evidence")
    opening, timestamp, price = final[-1]
    if not 0 <= now_ms - timestamp <= 60_000:
        raise ValueError("Stale source candle")
    if (
        indicators.get("lastCandleOpenTime") != opening
        or indicators.get("warnings") != []
        or _number(indicators.get("price")) != price
    ):
        raise ValueError("Indicator tape does not match completed evidence")
    measured = _object(indicators.get("indicators"))
    values = _object(measured.get("ema")).get("values")
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError("Missing EMA structure")
    averages = {}
    for raw in values:
        row = _object(raw)
        period = row.get("period")
        if type(period) is not int or period not in (20, 50, 200) or period in averages:
            raise ValueError("Ambiguous EMA period")
        averages[period] = _number(row.get("value"))
    adx = _object(measured.get("adx"))
    strength, plus, minus = (
        _number(adx.get(key), positive=False) for key in ("value", "plusDI", "minusDI")
    )
    if any(not 0 <= value <= 100 for value in (strength, plus, minus)):
        raise ValueError("Invalid directional measurements")
    contribution = (
        min(strength / 50, Decimal(1))
        if price > averages[20] > averages[50] > averages[200] and plus > minus
        else Decimal(0)
    )
    return {
        "symbol": symbol,
        "observations": [
            {
                "domain": "A",
                "strength": str(contribution),
                "timestamp_ms": timestamp,
                "origin": "binance_usdm_candles",
            }
        ],
    }


def _content_json(result: object) -> str:
    result = _object(result)
    if result.get("isError"):
        raise ValueError("Source tool unavailable")
    content = result.get("content")
    if not isinstance(content, list):
        raise ValueError("Original JSON content unavailable")
    candidates = [
        row.get("text")
        for raw in content
        if isinstance(raw, dict)
        for row in [raw]
        if row.get("type") == "text"
        and isinstance(row.get("text"), str)
        and row["text"].lstrip().startswith("{")
    ]
    if len(candidates) != 1 or not isinstance(candidates[0], str):
        raise ValueError("Ambiguous original source JSON")
    return candidates[0]


class TraderSpyProducer:
    def __init__(
        self,
        cache: ProviderEvidenceCache,
        execute: ReadOnlyToolExecutor,
        *,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
        timeout_seconds: float = 10,
    ) -> None:
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Invalid bounded source timeout")
        self.cache, self.execute, self.clock = cache, execute, clock
        self.timeout_seconds = timeout_seconds
        self.status: dict[str, str] = {}

    async def collect(self, symbol: str) -> bool:
        symbol = normalize_symbol(symbol)
        if symbol not in SIGNAL_SYMBOLS:
            raise ValueError("Unsupported source symbol")
        instrument = symbol.replace("/", "")
        try:
            candles, indicators = await asyncio.wait_for(
                asyncio.gather(
                    self.execute(
                        "traderspy_get_candles",
                        {"symbol": instrument, "interval": "1m", "limit": 3},
                    ),
                    self.execute(
                        "traderspy_get_technical_indicators",
                        {
                            "symbol": instrument,
                            "interval": "1m",
                            "indicators": ["ema", "adx"],
                            "history": 1,
                        },
                    ),
                ),
                self.timeout_seconds,
            )
            now = self.clock()
            report = adapt_traderspy(
                symbol, _content_json(candles), _content_json(indicators), now_ms=now
            )
            if not self.cache.update("TraderSpy", report, now_ms=now):
                raise ValueError("Rejected source report")
            self.status[symbol] = "ok"
            return True
        except asyncio.CancelledError:
            self.cache.update(
                "TraderSpy", {"symbol": symbol, "observations": []}, now_ms=self.clock()
            )
            self.status[symbol] = "provider_evidence_unavailable"
            raise
        except Exception:
            self.cache.update(
                "TraderSpy", {"symbol": symbol, "observations": []}, now_ms=self.clock()
            )
            self.status[symbol] = "provider_evidence_unavailable"
            return False
