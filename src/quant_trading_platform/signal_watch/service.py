"""Public completed-candle structure and isolated background candidate scanning."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from decimal import Decimal
from time import time_ns
from typing import Protocol

import httpx

from quant_trading_platform.market_data.derivatives import SIGNAL_SYMBOLS, exact_decimal
from quant_trading_platform.market_data.okx_candles import parse_completed_rows
from quant_trading_platform.signal_watch.engine import Frame, WatchEngine, detect_setups
from quant_trading_platform.signal_watch.intelligence import Observation, observation_max_age_ms
from quant_trading_platform.signal_watch.outcomes import OutcomeTracker
from quant_trading_platform.strategies.spot_momentum import DailyCandle


class StructureSource(Protocol):
    def fetch(self, symbol: str, *, now_ms: int) -> Frame: ...
    def close(self) -> None: ...


def _spot_flow(rows: list[object], now_ms: int, max_age_ms: int) -> tuple[Observation, int]:
    if not 2 <= len(rows) <= 3:
        raise ValueError("Insufficient bounded venue flow")
    venues: set[str] = set()
    clocks: list[int] = []
    imbalances: list[Decimal] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed flow evidence")
        venue = row.get("venue")
        timestamp, received = row.get("timestamp_ms"), row.get("received_at_ms")
        value = exact_decimal(row.get("depth_imbalance"), positive=False)
        if (
            not isinstance(venue, str)
            or venue not in {"binance", "bybit", "okx"}
            or venue in venues
            or type(timestamp) is not int
            or type(received) is not int
            or min(timestamp, received) <= 0
            or max(timestamp, received) > now_ms
            or now_ms - min(timestamp, received) > max_age_ms
            or value is None
            or not -1 <= value <= 1
        ):
            raise ValueError("Unverified venue flow")
        venues.add(venue)
        clocks.append(min(timestamp, received))
        imbalances.append(max(Decimal(0), value))
    timestamp = min(clocks)
    return (
        Observation(
            "Data Hub",
            "D",
            sum(imbalances, Decimal(0)) / len(imbalances),
            timestamp,
            "public_multi_venue_depth",
        ),
        timestamp + max_age_ms,
    )


class PublicStructureSource:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(trust_env=False)
        self.owns_client = client is None

    def _candles(
        self, symbol: str, bar: str, duration: int, now_ms: int
    ) -> tuple[DailyCandle, ...]:
        request = httpx.Request(
            "GET",
            "https://www.okx.com/api/v5/market/candles",
            params={"instId": symbol.replace("/", "-"), "bar": bar, "limit": "40"},
            extensions={"timeout": httpx.Timeout(5).as_dict()},
        )
        response = self.client.send(request, auth=None, follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != "0":
            raise ValueError("Unavailable candles")
        candles = parse_completed_rows(payload.get("data"))
        if len(candles) < 25:
            raise ValueError("Insufficient completed candles")
        for index, candle in enumerate(candles):
            if (
                candle.timestamp_ms <= 0
                or candle.timestamp_ms % duration != 0
                or candle.timestamp_ms + duration > now_ms
                or (index and candle.timestamp_ms != candles[index - 1].timestamp_ms + duration)
                or any(
                    not value.is_finite()
                    for value in (candle.open, candle.high, candle.low, candle.close, candle.volume)
                )
                or min(candle.open, candle.high, candle.low, candle.close) <= 0
                or candle.volume < 0
                or candle.low > min(candle.open, candle.close)
                or candle.high < max(candle.open, candle.close)
            ):
                raise ValueError("Invalid completed candle")
        if now_ms - (candles[-1].timestamp_ms + duration) >= duration:
            raise ValueError("Stale completed candles")
        return candles

    def fetch(self, symbol: str, *, now_ms: int) -> Frame:
        if symbol not in SIGNAL_SYMBOLS:
            raise ValueError("Unsupported structure symbol")
        try:
            candles = self._candles(symbol, "1m", 60_000, now_ms)
            higher = self._candles(symbol, "1H", 3_600_000, now_ms)
            current, previous = candles[-1], candles[-2]
            average = sum((c.close for c in higher[-20:]), Decimal(0)) / 20
            prior_average = sum((c.close for c in higher[-21:-1]), Decimal(0)) / 20
            trend = Decimal(1) if higher[-1].close > average > prior_average else Decimal(0)
            average_volume = sum((c.volume for c in candles[-21:-1]), Decimal(0)) / 20
            if average_volume <= 0:
                raise ValueError("Insufficient volume")
            return Frame(
                current.timestamp_ms + 60_000,
                current.close,
                previous.close,
                current.high,
                current.low,
                min(c.low for c in candles[-22:-2]),
                max(c.high for c in candles[-22:-2]),
                trend,
                current.volume / average_volume,
                current.close / candles[-6].close - 1,
                previous.high,
                previous.low,
            )
        except Exception:
            raise ValueError("Public structure unavailable") from None

    def close(self) -> None:
        if self.owns_client:
            self.client.close()


class WatchService:
    def __init__(
        self,
        engine: WatchEngine,
        source: StructureSource,
        evidence: Callable[[str, int], dict[str, object]],
        *,
        external: Callable[[str], Sequence[Observation]] = lambda symbol: (),
        collect_external: Callable[[str], Awaitable[bool]] | None = None,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
        interval_seconds: int = 30,
        max_flow_age_ms: int = 1_000,
    ) -> None:
        if interval_seconds < 15:
            raise ValueError("Unsafe structure polling interval")
        if type(max_flow_age_ms) is not int or max_flow_age_ms <= 0:
            raise ValueError("Invalid flow freshness limit")
        self.max_flow_age_ms = max_flow_age_ms
        self.engine, self.source, self.evidence = engine, source, evidence
        self.external, self.clock, self.interval_seconds = external, clock, interval_seconds
        self.collect_external = collect_external
        self.assets: dict[str, dict[str, object]] = {}
        self.outcomes = OutcomeTracker(engine)
        self._calibration = self.outcomes.calibration()
        self._observed_calibration = engine.calibration()
        self._diagnostics = engine.recent_diagnostics()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def poll_once(self) -> None:
        # Slow/failed assets have independent tasks; GET never calls this method.
        await asyncio.gather(*(self._poll_asset(symbol) for symbol in SIGNAL_SYMBOLS))
        self._diagnostics = self.engine.recent_diagnostics()
        self._observed_calibration = self.engine.calibration()

    async def _poll_asset(self, symbol: str) -> None:
        try:
            now = self.clock()
            frame = await asyncio.to_thread(self.source.fetch, symbol, now_ms=now)
            warnings = []
            collection_ok = True
            if self.collect_external is not None:
                try:
                    if not await asyncio.wait_for(self.collect_external(symbol), timeout=10):
                        collection_ok = False
                        warnings.append("external_collection_unavailable")
                except Exception:
                    collection_ok = False
                    warnings.append("external_collection_unavailable")
            now = self.clock()
            evidence = self.evidence(symbol, now)
            quality = evidence.get("quality")
            status = str(quality.get("status")) if isinstance(quality, dict) else "insufficient"
            try:
                observations = list(self.external(symbol)) if collection_ok else []
                if any(not isinstance(item, Observation) for item in observations):
                    raise ValueError("Malformed external evidence")
            except Exception:
                observations = []
                warnings.append("external_evidence_unavailable")
            if detect_setups(frame):
                observations.extend(
                    Observation(
                        "Market Structure",
                        domain,
                        strength,
                        frame.timestamp_ms,
                        "okx_completed_candles",
                    )
                    for domain, strength in (
                        ("A", max(frame.trend, Decimal(0))),
                        ("B", Decimal(1)),
                        ("C", Decimal(1)),
                    )
                )
            spot = evidence.get("spot")
            rows = spot.get("venues") if isinstance(spot, dict) else None
            flow_deadline = None
            if isinstance(rows, list) and len(rows) >= 2:
                try:
                    flow, flow_deadline = _spot_flow(rows, now, self.max_flow_age_ms)
                    observations.append(flow)
                except ValueError:
                    warnings.append("flow_evidence_unavailable")
            candidates = self.engine.scan(
                symbol,
                frame,
                observations,
                data_hub_quality=status,
                now_ms=now,
                market_regime="trend" if frame.trend else "range",
            )
            self.outcomes.observe(
                symbol, price=frame.close, timestamp_ms=frame.timestamp_ms, now_ms=now
            )
            for candidate in candidates:
                self.outcomes.register(candidate, entry_price=frame.close)
            self._calibration = self.outcomes.calibration()
            deadlines = [frame.timestamp_ms + 60_000] + [
                observation.timestamp_ms + observation_max_age_ms(observation)
                for candidate in candidates
                for observation in candidate.result.evidence
            ]
            if flow_deadline is not None and any(
                observation.origin == "public_multi_venue_depth"
                for candidate in candidates
                for observation in candidate.result.evidence
            ):
                deadlines.append(flow_deadline)
            self.assets[symbol] = {
                "status": "ok",
                "timestamp_ms": now,
                "valid_until_ms": min(deadlines),
                "candidates": [c.payload() | {"signal_id": c.signal_id} for c in candidates],
                "warnings": warnings,
            }
        except Exception:
            self.assets[symbol] = {
                "status": "error",
                "error": "structure_unavailable",
                "timestamp_ms": self.clock(),
                "candidates": [],
            }

    def snapshot(self) -> dict[str, object]:
        assets = copy.deepcopy(self.assets)
        now = self.clock()
        for state in assets.values():
            timestamp = state.get("timestamp_ms")
            valid_until = state.get("valid_until_ms")
            if state.get("status") == "ok" and (
                type(timestamp) is not int
                or not 0 <= now - timestamp <= 60_000
                or type(valid_until) is not int
                or now > valid_until
            ):
                state["status"], state["candidates"] = "stale", []
        return {
            "generated_at_ms": now,
            "paper_only": True,
            "live_execution": False,
            "assets": assets,
            "journal": {
                "kind": "historical_candidates",
                "limit": 100,
                "rows": copy.deepcopy(self._diagnostics),
            },
            "observed_outcomes": {
                "kind": "observed_net_return",
                "calibration": copy.deepcopy(self._observed_calibration),
            },
            "outcomes": {
                "kind": "estimated_forward_markout",
                "calibration": copy.deepcopy(self._calibration),
            },
        }

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.poll_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self.interval_seconds)

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="crypto-signal-watch-v4")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
        self.source.close()
