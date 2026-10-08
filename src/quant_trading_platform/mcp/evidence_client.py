"""A bounded, credential-free GET-only bridge to the approved evidence endpoint."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from time import time_ns
from urllib.parse import quote, urlsplit

import httpx

from quant_trading_platform.market_data.derivatives import (
    SIGNAL_SYMBOLS,
    exact_decimal,
    normalize_derivatives_snapshot,
)
from quant_trading_platform.models import Venue


class EvidenceUnavailable(RuntimeError):
    """Sanitized failure; unavailable evidence cannot count as confirmation."""


# Positive output allowlist: never forward unexpected auth, traces or raw response fields.
_FIELDS = frozenset(
    [
        "symbol",
        "generated_at_ms",
        "spot",
        "derivatives",
        "liquidations",
        "quality",
        "execution",
        "liquidation_sources",
        "last_received_at_ms",
        "venues",
        "cross_venue_spread",
        "midpoint_range_pct",
        "contributing_venues",
        "venue",
        "bid",
        "ask",
        "timestamp_ms",
        "received_at_ms",
        "timestamp_source",
        "depth_imbalance",
        "instrument_id",
        "mark_price",
        "index_price",
        "funding_rate",
        "next_funding_time_ms",
        "open_interest",
        "open_interest_unit",
        "source_fields",
        "mark_spot_basis_pct",
        "index_mark_deviation_pct",
        "funding_consensus",
        "median",
        "min",
        "max",
        "warning",
        "open_interest_change",
        "change",
        "change_pct",
        "unit",
        "mark_spot_basis",
        "basis_pct",
        "window_5m",
        "window_15m",
        "window_1h",
        "event_count",
        "long_quantity",
        "short_quantity",
        "quantity_unit",
        "largest_event_quantity",
        "latest_event_timestamp_ms",
        "source_completeness",
        "comparable",
        "cross_venue_notional",
        "status",
        "fresh_sources",
        "expected_sources",
        "missing",
        "stale",
        "warnings",
        "paper_only",
        "live_execution",
        "error",
    ]
)
_MONEY = frozenset(
    [
        "bid",
        "ask",
        "midpoint_range_pct",
        "depth_imbalance",
        "mark_price",
        "index_price",
        "funding_rate",
        "open_interest",
        "mark_spot_basis_pct",
        "index_mark_deviation_pct",
        "median",
        "min",
        "max",
        "change",
        "change_pct",
        "basis_pct",
        "long_quantity",
        "short_quantity",
        "largest_event_quantity",
        "cross_venue_notional",
    ]
)


def _safe(value: object, key: str = "") -> object:
    if key in _MONEY and value is not None:
        if not isinstance(value, str):
            raise ValueError("Expected decimal string")
        try:
            if not Decimal(value).is_finite():
                raise ValueError("Non-finite evidence value")
        except InvalidOperation:
            raise ValueError("Invalid evidence value") from None
    if isinstance(value, dict):
        return {
            name: _safe(child, name)
            for name, child in value.items()
            if isinstance(name, str) and name in _FIELDS
        }
    if isinstance(value, list):
        return [_safe(child) for child in value]
    if isinstance(value, float):
        raise ValueError("Inexact evidence value")
    if key == "error" and value not in (
        None,
        "public_liquidations_unavailable",
        "invalid_liquidation_evidence",
    ):
        return "signal_evidence_unavailable"
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise ValueError("Invalid evidence value")


def _validate(value: object, symbol: str, now_ms: int) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("symbol") != symbol:
        raise ValueError("Evidence identity mismatch")
    timestamp = value.get("generated_at_ms")
    if type(timestamp) is not int or timestamp <= 0 or not 0 <= now_ms - timestamp <= 5000:
        raise ValueError("Evidence response timestamp invalid")
    execution = value.get("execution")
    if not isinstance(execution, dict) or (
        execution.get("paper_only") is not True or execution.get("live_execution") is not False
    ):
        raise ValueError("Execution lock mismatch")
    quality = value.get("quality")
    if not isinstance(quality, dict) or quality.get("status") not in (
        "healthy",
        "degraded",
        "insufficient",
    ):
        raise ValueError("Invalid evidence quality")
    expected_count = quality.get("expected_sources")
    if (
        expected_count not in (2, 6)
        or type(expected_count) is not int
        or type(quality.get("fresh_sources")) is not int
    ):
        raise ValueError("Invalid evidence counts")
    # Two-source packets are permitted only for the explicit OKX-only market scope.
    expected_venues = {"okx"} if expected_count == 2 else {"binance", "bybit", "okx"}
    counts: list[int] = []
    fresh_labels: set[str] = set()
    for kind in ("spot", "derivatives"):
        section = value.get(kind)
        required_section = (
            {"venues", "cross_venue_spread"}
            if kind == "spot"
            else {"venues", "funding_consensus", "open_interest_change", "mark_spot_basis"}
        )
        if not isinstance(section, dict) or not required_section.issubset(section):
            raise ValueError("Missing evidence class")
        if not isinstance(section["venues"], list):
            raise ValueError("Missing source rows")
        venues: set[str] = set()
        for row in section["venues"]:
            if not isinstance(row, dict) or row.get("venue") not in expected_venues:
                raise ValueError("Invalid source venue")
            venue = row["venue"]
            if venue in venues or row.get("contributing_venues") != [venue]:
                raise ValueError("Repeated or untrusted source provenance")
            venues.add(venue)
            source_time, received_time = row.get("timestamp_ms"), row.get("received_at_ms")
            limit = 1000 if kind == "spot" else 360_000
            if (
                type(source_time) is not int
                or type(received_time) is not int
                or source_time <= 0
                or source_time > received_time
                or received_time > timestamp
                or now_ms - source_time > limit
            ):
                raise ValueError("Invalid or stale evidence source")
            if kind == "spot":
                if row.get("timestamp_source") not in ("exchange", "request_start", "receipt"):
                    raise ValueError("Missing timestamp provenance")
                bid, ask = exact_decimal(row.get("bid")), exact_decimal(row.get("ask"))
                if bid is None or ask is None or bid > ask or "depth_imbalance" not in row:
                    raise ValueError("Missing or invalid spot prices")
            else:
                fields = {
                    "venue",
                    "symbol",
                    "instrument_id",
                    "timestamp_ms",
                    "received_at_ms",
                    "mark_price",
                    "index_price",
                    "funding_rate",
                    "next_funding_time_ms",
                    "open_interest",
                    "open_interest_unit",
                    "source_fields",
                }
                if not fields.issubset(row) or row["symbol"] != symbol:
                    raise ValueError("Missing or misidentified derivative snapshot")
                if (
                    not isinstance(row["source_fields"], list)
                    or not row["source_fields"]
                    or any(not isinstance(field, str) for field in row["source_fields"])
                ):
                    raise ValueError("Missing source provenance")
                normalized = normalize_derivatives_snapshot(
                    **{
                        **{name: row[name] for name in fields},
                        "venue": Venue(venue),
                    }
                )
                expected_unit = "contracts" if venue == "okx" else "base_asset"
                if normalized.open_interest_unit != expected_unit:
                    raise ValueError("Incompatible native OI unit")
                if not {"mark_spot_basis_pct", "index_mark_deviation_pct"}.issubset(row):
                    raise ValueError("Missing derived evidence")
            fresh_labels.add(f"{kind}:{venue}")
        counts.append(len(venues))
    if quality["fresh_sources"] != sum(counts):
        raise ValueError("Untrusted evidence counts")
    labels: set[str] = set()
    for name in ("missing", "stale", "warnings"):
        items = quality.get(name)
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise ValueError("Missing evidence quality reasons")
        if name != "warnings":
            if len(set(items)) != len(items) or labels.intersection(items):
                raise ValueError("Repeated quality reasons")
            labels.update(items)
    expected_labels = {
        f"{kind}:{venue}"
        for kind in ("spot", "derivatives")
        for venue in expected_venues
    }
    if labels != expected_labels - fresh_labels:
        raise ValueError("Inconsistent missing/stale evidence")
    minimum = 1 if expected_count == 2 else 2
    expected_status = (
        "insufficient" if min(counts) < minimum else "degraded" if labels else "healthy"
    )
    if quality["status"] != expected_status:
        raise ValueError("Untrusted deterministic quality")
    liquidations = value.get("liquidations")
    if not isinstance(liquidations, dict):
        raise ValueError("Missing liquidation evidence")
    for name in ("window_5m", "window_15m", "window_1h"):
        window = liquidations.get(name)
        if (
            not isinstance(window, dict)
            or not isinstance(window.get("venues"), list)
            or (
                window.get("comparable") is not False
                or "cross_venue_notional" not in window
                or window["cross_venue_notional"] is not None
            )
        ):
            raise ValueError("Invalid liquidation window")
        seen: set[str] = set()
        for row in window["venues"]:
            if not isinstance(row, dict) or row.get("venue") not in ("binance", "bybit", "okx"):
                raise ValueError("Invalid liquidation venue")
            venue = row["venue"]
            if venue in seen:
                raise ValueError("Duplicate liquidation source")
            seen.add(venue)
            count = row.get("event_count")
            if type(count) is not int or not 1 <= count <= 10_000:
                raise ValueError("Invalid liquidation count")
            duration = {"window_5m": 300_000, "window_15m": 900_000, "window_1h": 3_600_000}[name]
            event_time = row.get("latest_event_timestamp_ms")
            if (
                type(event_time) is not int
                or not 0 < event_time <= timestamp
                or (timestamp - event_time > duration)
            ):
                raise ValueError("Invalid liquidation timestamp")
            expected_unit = "contracts" if venue == "okx" else "base_asset"
            if row.get("quantity_unit") != expected_unit or (
                row.get("source_completeness") != "partial_exchange_stream"
            ):
                raise ValueError("Invalid liquidation provenance")
            for field in ("long_quantity", "short_quantity", "largest_event_quantity"):
                number = row.get(field)
                parsed = exact_decimal(number, positive=field == "largest_event_quantity")
                if parsed is None or parsed < 0:
                    raise ValueError("Invalid liquidation size")
    sanitized = _safe(value)
    assert isinstance(sanitized, dict)
    return sanitized


class EvidenceClient:
    def __init__(
        self,
        origin: str = "http://127.0.0.1:8000",
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
        hosted: bool = False,
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or (
                parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            )
        ):
            raise ValueError("Evidence origin must be loopback HTTP or explicit HTTPS")
        # Force port syntax validation before requests; never accept a user-supplied path.
        _ = parsed.port
        self.origin = origin.rstrip("/")
        self._client = client
        self._owns_client = client is None
        self.clock = clock
        if type(hosted) is not bool:
            raise ValueError("Invalid hosted gateway selection")
        self._prefix = "/api" if hosted else ""

    async def get_signal_evidence(self, symbol: str) -> dict[str, object]:
        if symbol not in SIGNAL_SYMBOLS:
            raise ValueError("Unsupported signal evidence symbol")
        if self._client is None:
            self._client = httpx.AsyncClient(trust_env=False)
        request = httpx.Request(
            "GET",
            self.origin + self._prefix + "/signal-evidence/" + quote(symbol, safe=""),
            extensions={"timeout": httpx.Timeout(5).as_dict()},
        )
        try:
            async with asyncio.timeout(5):
                response = await self._client.send(
                    request, auth=None, follow_redirects=False, stream=True
                )
                try:
                    if response.status_code != 200:
                        raise ValueError("Evidence unavailable")
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(chunks) + len(chunk) > 262_144:
                            raise ValueError("Evidence too large")
                        chunks.extend(chunk)
                    return _validate(json.loads(chunks), symbol, self.clock())
                finally:
                    await response.aclose()
        except Exception:
            raise EvidenceUnavailable("signal_evidence_unavailable") from None

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
