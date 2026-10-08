"""Exact-horizon price markouts with disclosed estimated costs, never execution P&L."""

import json
from decimal import Decimal

from quant_trading_platform.market_data.derivatives import exact_decimal
from quant_trading_platform.signal_watch.engine import WatchEngine
from quant_trading_platform.signal_watch.journal import Candidate


class OutcomeTracker:
    def __init__(
        self,
        engine: WatchEngine,
        *,
        horizon_ms: int = 900_000,
        fee_rate: Decimal = Decimal("0.001"),
        slippage_rate: Decimal = Decimal("0.0005"),
    ) -> None:
        for value in (fee_rate, slippage_rate):
            if not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value < 1:
                raise ValueError("Invalid estimated cost")
        if type(horizon_ms) is not int or horizon_ms <= 0 or fee_rate + slippage_rate >= 1:
            raise ValueError("Invalid markout configuration")
        self.engine, self.horizon_ms = engine, horizon_ms
        self.fee_rate, self.slippage_rate = fee_rate, slippage_rate
        with engine.journal.connection:
            engine.journal.connection.execute(
                "CREATE TABLE IF NOT EXISTS forward_markouts "
                "(signal_id TEXT PRIMARY KEY, asset TEXT NOT NULL, entry_price TEXT NOT NULL, "
                "due_ms INTEGER NOT NULL, fee_rate TEXT NOT NULL, slippage_rate TEXT NOT NULL, "
                "status TEXT NOT NULL, exit_price TEXT, net_return TEXT)"
            )

    def register(self, candidate: Candidate, *, entry_price: Decimal) -> None:
        price = exact_decimal(entry_price)
        if price is None:
            raise ValueError("Missing entry price")
        if candidate.result.rejected_reason in ("setup_not_confirmed", "stale_structure"):
            return
        self.engine.journal.record(candidate)
        with self.engine.journal.connection:
            self.engine.journal.connection.execute(
                "INSERT OR IGNORE INTO forward_markouts VALUES (?,?,?,?,?,?,?,NULL,NULL)",
                (
                    candidate.signal_id,
                    candidate.asset,
                    str(price),
                    candidate.timestamp_ms + self.horizon_ms,
                    str(self.fee_rate),
                    str(self.slippage_rate),
                    "pending",
                ),
            )

    def observe(self, asset: str, *, price: Decimal, timestamp_ms: int, now_ms: int) -> None:
        exact = exact_decimal(price)
        if exact is None or type(timestamp_ms) is not int or type(now_ms) is not int:
            raise ValueError("Invalid observed markout price")
        if not 0 <= now_ms - timestamp_ms <= 60_000:
            return
        connection = self.engine.journal.connection
        rows = connection.execute(
            "SELECT signal_id, entry_price, due_ms, fee_rate, slippage_rate "
            "FROM forward_markouts WHERE asset=? AND status=?",
            (asset, "pending"),
        ).fetchall()
        for signal_id, entry, due, fee, slippage in rows:
            if timestamp_ms < due:
                continue
            with connection:
                if timestamp_ms > due:
                    connection.execute(
                        "UPDATE forward_markouts SET status=? WHERE signal_id=?",
                        ("missed_horizon", signal_id),
                    )
                    continue
                costs = Decimal(fee) + Decimal(slippage)
                net_return = exact * (1 - costs) / (Decimal(entry) * (1 + costs)) - 1
                # Estimated markouts never overwrite or reserve observed-outcome identities.
                connection.execute(
                    "UPDATE forward_markouts SET status=?, exit_price=?, "
                    "net_return=? WHERE signal_id=?",
                    ("measured", str(exact), str(net_return), signal_id),
                )

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "signal_id": row[0],
                "asset": row[1],
                "due_ms": row[2],
                "status": row[3],
                "net_return": row[4],
                "kind": "estimated_forward_markout",
            }
            for row in self.engine.journal.connection.execute(
                "SELECT signal_id, asset, due_ms, status, net_return FROM forward_markouts "
                "ORDER BY rowid"
            )
        ]

    def calibration(self) -> dict[str, object]:
        buckets: dict[str, list[Decimal]] = {
            label: [] for label in ("HIGH", "VERY HIGH", "REJECTED")
        }
        for payload, net_return in self.engine.journal.connection.execute(
            "SELECT candidates.payload, forward_markouts.net_return FROM forward_markouts "
            "JOIN candidates USING(signal_id) WHERE forward_markouts.status=?",
            ("measured",),
        ):
            buckets[json.loads(payload)["confidence"]].append(Decimal(net_return))
        return {
            label: {
                "samples": len(values),
                "positive": sum(value > 0 for value in values),
                "mean_net_return": None
                if not values
                else str(sum(values, Decimal(0)) / len(values)),
            }
            for label, values in buckets.items()
        }
