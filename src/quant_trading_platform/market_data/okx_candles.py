"""Public, keyless OKX UTC candles (daily and intraday) for research only."""

from decimal import Decimal, InvalidOperation
from typing import cast

import httpx

from quant_trading_platform.strategies.spot_momentum import (
    BAR_MS,
    RESEARCH_SYMBOLS,
    DailyCandle,
    validate_candles,
)


class OKXCandleSource:
    endpoint = "https://www.okx.com/api/v5/market/candles"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(trust_env=False, timeout=5.0)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch(self, symbol: str, *, now_ms: int, bar: str = "1Dutc") -> tuple[DailyCandle, ...]:
        if symbol not in RESEARCH_SYMBOLS:
            raise ValueError("Unsupported OKX USDT spot pair")
        if bar not in BAR_MS:
            raise ValueError("Unsupported OKX candle interval")
        interval_ms = BAR_MS[bar]
        request = httpx.Request(
            "GET", self.endpoint,
            params={"instId": symbol.replace("/", "-"), "bar": bar, "limit": "200"},
            extensions={"timeout": httpx.Timeout(5.0).as_dict()},
        )
        try:
            response = self._client.send(request, auth=None, follow_redirects=False)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise ValueError("OKX public candles unavailable") from None
        if not isinstance(payload, dict) or payload.get("code") != "0":
            raise ValueError("OKX public candles unavailable")
        result = parse_completed_rows(payload.get("data"))
        # The latest candle must be the last completed bar in UTC. Exclude the open bar.
        # A missing bar or a mixed timezone fails the entire research snapshot.
        if result and result[-1].timestamp_ms > now_ms - interval_ms:
            raise ValueError("Incomplete OKX candle")
        validate_candles(result, now_ms=now_ms, interval_ms=interval_ms)
        return result


def parse_completed_rows(rows: object) -> tuple[DailyCandle, ...]:
    """Parse one public OKX page without trusting its order or an open candle."""
    if not isinstance(rows, list):
        raise ValueError("Malformed OKX daily candles")
    candles: list[DailyCandle] = []
    try:
        for raw in rows:
            if not isinstance(raw, list) or len(raw) != 9 or raw[8] not in ("0", "1"):
                raise ValueError("Malformed OKX daily candle")
            if raw[8] != "1":
                continue
            values = cast(list[str], raw)
            if not all(isinstance(value, str) for value in values):
                raise ValueError("Malformed OKX daily candle")
            candles.append(DailyCandle(
                int(values[0]), *(Decimal(value) for value in values[1:6]),
            ))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Malformed OKX daily candles") from None
    candles.sort(key=lambda item: item.timestamp_ms)
    return tuple(candles)
