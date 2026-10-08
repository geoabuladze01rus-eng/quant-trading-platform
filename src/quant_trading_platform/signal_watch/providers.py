"""Strict normalized report boundary for authorized hosted provider adapters.

This does not infer timestamps or confidence from prose/AI directives. The producer must
supply an auditable observation report, not a plugin's unqualified trading recommendation.
"""

import json
from decimal import Decimal
from threading import RLock

from quant_trading_platform.market_data.derivatives import SIGNAL_SYMBOLS, exact_decimal
from quant_trading_platform.models import normalize_symbol
from quant_trading_platform.signal_watch.intelligence import (
    PROVIDERS,
    Observation,
    observation_max_age_ms,
)


class ProviderEvidenceCache:
    def __init__(self) -> None:
        self._lock = RLock()
        self._values: dict[tuple[str, str], tuple[Observation, ...]] = {}
        self._statuses: dict[tuple[str, str], str] = {}

    def update(self, source: str, payload: object, *, now_ms: int) -> bool:
        with self._lock:
            return self._update(source, payload, now_ms=now_ms)

    def _update(self, source: str, payload: object, *, now_ms: int) -> bool:
        if source not in PROVIDERS or type(now_ms) is not int or now_ms <= 0:
            raise ValueError("Invalid provider/clock")
        symbol = ""
        try:
            if isinstance(payload, str):
                if len(payload) > 1_048_576:
                    raise ValueError("Oversize report")
                payload = json.loads(payload, parse_float=Decimal)
            if not isinstance(payload, dict) or not isinstance(payload.get("symbol"), str):
                raise ValueError("Missing identity")
            symbol = normalize_symbol(payload["symbol"])
            if symbol not in SIGNAL_SYMBOLS:
                raise ValueError("Unsupported report symbol")
            raw = payload.get("observations")
            if not isinstance(raw, list) or not 1 <= len(raw) <= 5:
                raise ValueError("Missing auditable observations")
            observations = []
            for row in raw:
                if not isinstance(row, dict):
                    raise ValueError("Invalid observation")
                domain, origin, timestamp = (
                    row.get("domain"),
                    row.get("origin"),
                    row.get("timestamp_ms"),
                )
                strength = exact_decimal(row.get("strength"), positive=False)
                if (
                    not isinstance(domain, str)
                    or not isinstance(origin, str)
                    or type(timestamp) is not int
                    or strength is None
                    or not 0 <= now_ms - timestamp <= 60_000
                ):
                    raise ValueError("Unverified evidence/freshness")
                observation = Observation(source, domain, strength, timestamp, origin)
                if now_ms - timestamp > observation_max_age_ms(observation):
                    raise ValueError("Stale source evidence")
                observations.append(observation)
            if len({o.domain for o in observations}) != len(observations):
                raise ValueError("Duplicate provider domains")
            self._values[source, symbol] = tuple(observations)
            self._statuses[source, symbol] = "ok"
            return True
        except (ValueError, TypeError):
            if symbol in SIGNAL_SYMBOLS:
                self._values.pop((source, symbol), None)
                self._statuses[source, symbol] = "error"
            else:
                # A source response without trusted identity invalidates its cached reports.
                for key in tuple(self._values):
                    if key[0] == source:
                        del self._values[key]
                        self._statuses[key] = "error"
            return False

    def observations(self, symbol: str, *, now_ms: int) -> tuple[Observation, ...]:
        symbol = normalize_symbol(symbol)
        with self._lock:
            return tuple(
                o
                for (source, asset), values in self._values.items()
                if asset == symbol and self.status(source, asset, now_ms=now_ms) == "ok"
                for o in values
            )

    def status(self, source: str, symbol: str, *, now_ms: int) -> str:
        with self._lock:
            return self._status(source, symbol, now_ms=now_ms)

    def _status(self, source: str, symbol: str, *, now_ms: int) -> str:
        key = source, normalize_symbol(symbol)
        status = self._statuses.get(key, "no_data")
        if status == "ok" and any(
            not 0 <= now_ms - o.timestamp_ms <= observation_max_age_ms(o) for o in self._values[key]
        ):
            return "stale"
        return status

    def snapshot(self, *, now_ms: int) -> list[dict[str, object]]:
        with self._lock:
            return [
                {
                    "source": source,
                    "symbol": symbol,
                    "status": self.status(source, symbol, now_ms=now_ms),
                }
                for source in sorted(PROVIDERS - {"Market Structure", "Data Hub"})
                for symbol in SIGNAL_SYMBOLS
            ]
