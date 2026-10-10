"""Private local signal inbox and demo lifecycle loop; no public command endpoint."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quant_trading_platform.paper_trading.okx_demo import (
    ConfirmedDemoSignal,
    DemoIntent,
    OKXDemoBot,
)


def parse_signal(data: Any) -> ConfirmedDemoSignal:
    """Require exact JSON fields/types. Money must be decimal strings."""
    fields = {
        "signal_id",
        "symbol",
        "entry",
        "quantity",
        "stop",
        "take_profit",
        "expires_at_ms",
        "confidence",
        "confirmations",
        "observed_at_ms",
        "news_checked_at_ms",
        "higher_timeframes_aligned",
        "news_blocked",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("Некорректный формат подтверждённого сигнала")
    strings = ("signal_id", "symbol", "entry", "quantity", "stop", "take_profit", "confidence")
    integers = ("expires_at_ms", "observed_at_ms", "news_checked_at_ms")
    booleans = ("higher_timeframes_aligned", "news_blocked")
    confirmations = data["confirmations"]
    if (
        any(type(data[k]) is not str for k in strings)
        or any(type(data[k]) is not int for k in integers)
        or any(type(data[k]) is not bool for k in booleans)
        or not isinstance(confirmations, list)
        or any(type(c) is not str for c in confirmations)
        or len(confirmations) != len(set(confirmations))
    ):
        raise ValueError("Некорректные типы полей сигнала")
    try:
        intent = DemoIntent(
            data["signal_id"],
            data["symbol"],
            Decimal(data["entry"]),
            Decimal(data["quantity"]),
            Decimal(data["stop"]),
            Decimal(data["take_profit"]),
            data["expires_at_ms"],
        )
    except InvalidOperation:
        raise ValueError("Некорректные денежные значения сигнала") from None
    return ConfirmedDemoSignal(
        intent,
        data["confidence"],
        frozenset(confirmations),
        data["observed_at_ms"],
        data["news_checked_at_ms"],
        data["higher_timeframes_aligned"],
        data["news_blocked"],
    )


class DemoRunner:
    """Manages known orders even when new entries are disabled.

    Source and output are local server paths. A strategy must actually establish
    the confirmations; this runner cannot turn evidence observations into signals.
    """

    def __init__(
        self,
        bot: OKXDemoBot,
        signal_file: Path | None,
        *,
        entries_enabled: bool = False,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.bot, self.signal_file, self.entries_enabled = bot, signal_file, entries_enabled
        self.emit = emit
        self.bot.db.execute(
            "CREATE TABLE IF NOT EXISTS demo_events "
            "(id INTEGER PRIMARY KEY, signal_id TEXT NOT NULL, state TEXT NOT NULL, "
            "details TEXT NOT NULL)"
        )
        self.bot.db.commit()

    def _event(self, item: dict[str, Any]) -> None:
        signal_id = item["signal_id"]
        # Compare economic state, excluding heartbeat timestamps.
        details = json.dumps(
            {k: v for k, v in item.items() if k != "checked_at_ms"}, sort_keys=True
        )
        self.bot.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.bot.db.execute(
                "SELECT details FROM demo_events WHERE signal_id=? ORDER BY id DESC LIMIT 1",
                (signal_id,),
            ).fetchone()
            changed = previous is None or previous[0] != details
            if changed:
                self.bot.db.execute(
                    "INSERT INTO demo_events (signal_id, state, details) VALUES (?, ?, ?)",
                    (signal_id, item["state"], details),
                )
            self.bot.db.commit()
        except Exception:
            self.bot.db.rollback()
            raise
        if changed and self.emit is not None:
            self.emit(item)

    def tick(self) -> dict[str, Any]:
        states = self.bot.recover()
        for state in states:
            self._event(state)
        if not self.entries_enabled or self.signal_file is None:
            return {"status": "monitoring", "states": states, "demo_only": True}
        if any(s["state"] not in {"closed", "canceled_empty"} for s in states):
            return {"status": "entry_blocked", "states": states, "demo_only": True}
        try:
            if self.signal_file.stat().st_size > 16_384:
                raise ValueError("Слишком большой файл сигнала")
            signal = parse_signal(json.loads(self.signal_file.read_text(encoding="utf-8")))
            receipt = self.bot.execute_signal(signal)
        except FileNotFoundError:
            return {"status": "waiting_for_signal", "states": states, "demo_only": True}
        except (ValueError, RuntimeError, OSError):
            # Never print input text, filesystem contents, or authenticated responses.
            return {"status": "signal_rejected", "states": states, "demo_only": True}
        return {"status": "submitted", "receipt": receipt, "states": states, "demo_only": True}
