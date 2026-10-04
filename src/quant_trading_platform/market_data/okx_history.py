"""Bounded, keyless historical OKX spot candle download for offline research."""

from typing import cast

import httpx

from quant_trading_platform.market_data.okx_candles import parse_completed_rows
from quant_trading_platform.strategies.spot_momentum import (
    DAY_MS,
    SYMBOLS,
    DailyCandle,
    validate_candles,
)


class OKXHistorySource:
    endpoint = "https://www.okx.com/api/v5/market/history-candles"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(trust_env=False, timeout=10.0)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch(self, symbol: str, *, days: int, now_ms: int) -> tuple[DailyCandle, ...]:
        """Require exactly `days` continuous completed UTC candles, or fail closed."""
        if symbol not in SYMBOLS or type(days) is not int or not 102 <= days <= 4000:
            raise ValueError("Unsupported pair or history length")
        if type(now_ms) is not int or now_ms < 2 * DAY_MS:
            raise ValueError("Invalid clock")
        newest_open = now_ms // DAY_MS * DAY_MS - DAY_MS
        earliest_open = newest_open - (days - 1) * DAY_MS
        by_timestamp: dict[int, DailyCandle] = {}
        cursor: int | None = None
        for _ in range(50):
            params = {"instId": symbol.replace("/", "-"), "bar": "1Dutc", "limit": "100"}
            if cursor is not None:
                params["after"] = str(cursor)
            request = httpx.Request(
                "GET", self.endpoint, params=params,
                extensions={"timeout": httpx.Timeout(10.0).as_dict()},
            )
            try:
                response = self._client.send(request, auth=None, follow_redirects=False)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                raise ValueError("OKX historical candles unavailable") from None
            if not isinstance(payload, dict) or payload.get("code") != "0":
                raise ValueError("OKX historical candles unavailable")
            page = parse_completed_rows(cast(dict[str, object], payload).get("data"))
            if not page:
                break
            oldest = page[0].timestamp_ms
            if cursor is not None and oldest >= cursor:
                raise ValueError("OKX candle pagination did not advance")
            for candle in page:
                if earliest_open <= candle.timestamp_ms <= newest_open:
                    prior = by_timestamp.get(candle.timestamp_ms)
                    if prior is not None and prior != candle:
                        raise ValueError("Conflicting OKX historical candle")
                    by_timestamp[candle.timestamp_ms] = candle
            if oldest <= earliest_open:
                break
            cursor = oldest
        result = tuple(by_timestamp[ts] for ts in sorted(by_timestamp))
        if len(result) != days or result[0].timestamp_ms != earliest_open:
            raise ValueError("OKX historical candles are incomplete")
        validate_candles(result, now_ms=now_ms)
        return result
