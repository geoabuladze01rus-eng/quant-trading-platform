"""Candidate scan, diagnostic ANTI MISS and observed-outcome calibration.

ANTI MISS records near-threshold/blocked setups; it never bypasses confidence gates.
Calibration reports observations only and does not tune production thresholds.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import cast

from quant_trading_platform.signal_watch.intelligence import Observation, evaluate
from quant_trading_platform.signal_watch.journal import SETUPS, Candidate, Journal


@dataclass(frozen=True)
class Frame:
    timestamp_ms: int
    close: Decimal
    previous_close: Decimal
    high: Decimal
    low: Decimal
    support: Decimal
    resistance: Decimal
    trend: Decimal
    volume_ratio: Decimal
    momentum: Decimal
    previous_high: Decimal
    previous_low: Decimal
    atr: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        prices = (
            self.close,
            self.previous_close,
            self.high,
            self.low,
            self.support,
            self.resistance,
            self.previous_high,
            self.previous_low,
        )
        if (
            type(self.timestamp_ms) is not int
            or self.timestamp_ms <= 0
            or any(
                not isinstance(x, Decimal) or not x.is_finite()
                for x in (*prices, self.trend, self.volume_ratio, self.momentum, self.atr)
            )
            or min(prices) <= 0
            or self.low > self.close
            or self.high < self.close
            or self.low > self.high
            or self.support > self.resistance
            or self.previous_low > self.previous_high
            or self.volume_ratio < 0
            or abs(self.trend) > 1
            or self.atr < 0
        ):
            raise ValueError("Invalid market structure frame")


def detect_setups(frame: Frame) -> tuple[str, ...]:
    found = []
    tolerance = Decimal("0.003")
    if (
        frame.trend > 0
        and frame.support * (1 - tolerance) <= frame.low <= frame.support * (1 + tolerance)
        and frame.close > frame.support
        and frame.close > frame.previous_close
    ):
        found.append("Trend Pullback")
    if (
        frame.trend > 0
        and frame.previous_close > frame.resistance
        and frame.resistance * (1 - tolerance) <= frame.low <= frame.resistance * (1 + tolerance)
        and frame.close > frame.resistance
    ):
        found.append("Breakout + Retest")
    if frame.low < frame.previous_low and frame.close > frame.previous_low:
        found.append("Liquidity Sweep")
    if (
        frame.trend > 0
        and frame.momentum >= Decimal("0.01")
        and frame.volume_ratio >= Decimal("1.5")
        and frame.close > frame.previous_close
    ):
        found.append("Momentum")
    return tuple(found)


class WatchEngine:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        with journal.connection:
            journal.connection.execute(
                "CREATE TABLE IF NOT EXISTS missed_opportunities "
                "(signal_id TEXT PRIMARY KEY REFERENCES candidates(signal_id), "
                "reason TEXT NOT NULL)"
            )
            journal.connection.execute(
                "CREATE TABLE IF NOT EXISTS signal_outcomes "
                "(signal_id TEXT PRIMARY KEY REFERENCES candidates(signal_id), "
                "net_return TEXT NOT NULL, timestamp_ms INTEGER NOT NULL, confidence TEXT NOT NULL)"
            )

    def scan(
        self,
        asset: str,
        frame: Frame,
        observations: Sequence[Observation],
        *,
        data_hub_quality: str,
        now_ms: int,
        market_regime: str,
        allow_degraded: bool = False,
    ) -> tuple[Candidate, ...]:
        confirmed = detect_setups(frame)
        fresh = 0 <= now_ms - frame.timestamp_ms <= 60_000
        result = evaluate(
            observations,
            now_ms=now_ms,
            data_hub_quality=data_hub_quality,
            allow_degraded=allow_degraded,
        )
        candidates = []
        for setup in SETUPS:
            reason = (
                "stale_structure"
                if not fresh
                else "setup_not_confirmed" if setup not in confirmed else result.rejected_reason
            )
            assessment = (
                replace(result, confidence="REJECTED", rejected_reason=reason) if reason else result
            )
            candidate = Candidate(asset, setup, assessment, market_regime, frame.timestamp_ms)
            signal_id = self.journal.record(candidate)
            candidates.append(candidate)
            if fresh and setup in confirmed and reason:
                missed_reason = "near_threshold" if 65 <= result.score < 72 else reason
                with self.journal.connection:
                    self.journal.connection.execute(
                        "INSERT OR IGNORE INTO missed_opportunities VALUES (?, ?)",
                        (signal_id, missed_reason),
                    )
        return tuple(candidates)

    def missed_opportunities(self) -> list[dict[str, object]]:
        return [
            {"signal_id": row[0], "reason": row[1]}
            for row in self.journal.connection.execute(
                "SELECT signal_id, reason FROM missed_opportunities ORDER BY rowid"
            )
        ]

    def recent_diagnostics(self) -> list[dict[str, object]]:
        """Bounded historical candidates with ANTI MISS reasons, newest insertion first."""
        import json

        return [
            cast(dict[str, object], json.loads(payload))
            | {"signal_id": signal_id, "missed_reason": missed_reason}
            for signal_id, payload, missed_reason in self.journal.connection.execute(
                "SELECT candidates.signal_id, candidates.payload, missed_opportunities.reason "
                "FROM candidates LEFT JOIN missed_opportunities USING(signal_id) "
                "ORDER BY candidates.rowid DESC LIMIT 100"
            )
        ]

    def record_outcome(self, signal_id: str, *, net_return: Decimal, timestamp_ms: int) -> None:
        if (
            not isinstance(net_return, Decimal)
            or not net_return.is_finite()
            or type(timestamp_ms) is not int
            or timestamp_ms <= 0
        ):
            raise ValueError("Invalid observed outcome")
        import json

        row = self.journal.connection.execute(
            "SELECT payload FROM candidates WHERE signal_id=?", (signal_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown candidate")
        payload = json.loads(row[0])
        if timestamp_ms <= payload["timestamp_ms"]:
            raise ValueError("Outcome must follow candidate")
        with self.journal.connection:
            self.journal.connection.execute(
                "INSERT OR IGNORE INTO signal_outcomes VALUES (?,?,?,?)",
                (signal_id, str(net_return), timestamp_ms, payload["confidence"]),
            )

    def calibration(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for confidence in ("HIGH", "VERY HIGH", "REJECTED"):
            values = [
                Decimal(row[0])
                for row in self.journal.connection.execute(
                    "SELECT net_return FROM signal_outcomes WHERE confidence=?", (confidence,)
                )
            ]
            result[confidence] = {
                "samples": len(values),
                "positive": sum(x > 0 for x in values),
                "mean_net_return": (
                    None if not values else str(sum(values, Decimal(0)) / len(values))
                ),
            }
        return result
