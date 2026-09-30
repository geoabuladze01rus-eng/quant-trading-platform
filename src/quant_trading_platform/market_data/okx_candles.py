"""Public, keyless OKX UTC daily candles for research only."""

from decimal import Decimal, InvalidOperation
from typing import cast

import httpx

from quant_trading_platform.strategies.spot_momentum import (
    DAY_MS,
    SYMBOLS,
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

    def fetch(self, symbol: str, *, now_ms: int) -> tuple[DailyCandle, ...]:
        if symbol not in SYMBOLS:
            raise ValueError("Unsupported OKX USDT spot pair")
        request = httpx.Request(
            "GET", self.endpoint,
            params={"instId": symbol.replace("/", "-"), "bar": "1Dutc", "limit": "200"},
            extensions={"timeout": httpx.Timeout(5.0).as_dict()},
        )
        try:
            response = self._client.send(request, auth=None, follow_redirects=False)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise ValueError("OKX public daily candles unavailable") from None
        if not isinstance(payload, dict) or payload.get("code") != "0":
            raise ValueError("OKX public daily candles unavailable")
        rows = payload.get("data")
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
        # The latest candle must be yesterday in UTC. Exclude current incomplete bar.
        # A missing day or a mixed bar timezone fails the entire research snapshot.
        if candles and candles[-1].timestamp_ms > now_ms - DAY_MS:
            raise ValueError("Incomplete OKX daily candle")
        result = tuple(candles)
        validate_candles(result, now_ms=now_ms)
        return result
