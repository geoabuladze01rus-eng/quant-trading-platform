"""Durable candidate records; financial quantities are encoded as decimal strings."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from quant_trading_platform.market_data.derivatives import SIGNAL_SYMBOLS
from quant_trading_platform.signal_watch.intelligence import Confidence

SETUPS = ("Trend Pullback", "Breakout + Retest", "Liquidity Sweep", "Momentum")


@dataclass(frozen=True)
class Candidate:
    asset: str
    setup: str
    result: Confidence
    market_regime: str
    timestamp_ms: int

    def __post_init__(self) -> None:
        if (
            self.asset not in SIGNAL_SYMBOLS
            or self.setup not in SETUPS
            or not self.market_regime.strip()
            or type(self.timestamp_ms) is not int
            or self.timestamp_ms <= 0
        ):
            raise ValueError("Invalid candidate")

    def payload(self) -> dict[str, object]:
        return {
            "asset": self.asset,
            "setup": self.setup,
            "score": str(self.result.score),
            "domains": {k: str(v) for k, v in self.result.domains.items()},
            "confidence": self.result.confidence,
            "rejected_reason": self.result.rejected_reason,
            "market_regime": self.market_regime,
            "timestamp_ms": self.timestamp_ms,
            "sources": self.result.accepted_sources,
            "evidence": [
                {
                    "source": item.source,
                    "domain": item.domain,
                    "origin": item.origin,
                    "timestamp_ms": item.timestamp_ms,
                    "strength": str(item.strength),
                }
                for item in self.result.evidence
            ],
            "warnings": self.result.warnings,
            "paper_only": True,
            "live_execution": False,
        }

    @property
    def signal_id(self) -> str:
        return hashlib.sha256(json.dumps(self.payload(), sort_keys=True).encode()).hexdigest()


class Journal:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS candidates "
            "(signal_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self.connection.commit()

    def record(self, candidate: Candidate) -> str:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO candidates VALUES (?, ?)",
                (candidate.signal_id, json.dumps(candidate.payload())),
            )
        return candidate.signal_id

    def candidates(self) -> list[dict[str, object]]:
        return [
            cast(dict[str, object], json.loads(row[0]))
            for row in self.connection.execute("SELECT payload FROM candidates ORDER BY rowid")
        ]

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
