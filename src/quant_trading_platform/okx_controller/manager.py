"""Atomic local OKX paper bot control. No exchange write transport exists here."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from time import sleep, time_ns
from typing import cast

import yaml

LOG = logging.getLogger(__name__)


class ControlError(ValueError):
    pass


@contextmanager
def atomic(connection: sqlite3.Connection) -> Iterator[None]:
    owned = not connection.in_transaction
    if owned:
        connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        if owned:
            connection.commit()
    except BaseException:
        if owned:
            connection.rollback()
        raise


def money(value: Decimal, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("Financial values require finite nonnegative Decimal")
    if positive and value == 0:
        raise ValueError("Positive financial value required")
    return value


class BotManager:
    paper_only = True
    live_execution = False

    def __init__(
        self,
        connection: sqlite3.Connection,
        registry: Path,
        *,
        clock: Callable[[], int] = lambda: time_ns() // 1_000_000,
        sleep: Callable[[int], None] = sleep,
    ) -> None:
        self.connection, self.clock, self.sleep = connection, clock, sleep
        rows = yaml.safe_load(registry.read_text())
        if not isinstance(rows, list) or not rows:
            raise ValueError("Invalid bot registry")
        seen = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row)
                != {"bot_id", "symbol", "strategy", "status", "created_at", "last_signal"}
                or not all(isinstance(row[k], str) for k in row if k != "last_signal")
                or row["bot_id"] in seen
                or not row["bot_id"].strip()
                or row["symbol"] not in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BTC/ETH")
                or row["strategy"] not in ("GRID", "DCA", "PAIR")
                or row["status"] not in ("STOPPED", "DISABLED")
                or row["last_signal"] is not None
                or datetime.fromisoformat(row["created_at"]).tzinfo is None
            ):
                raise ValueError("Invalid bot registry entry")
            seen.add(row["bot_id"])
        with atomic(connection):
            connection.execute(
                "CREATE TABLE IF NOT EXISTS paper_bots ("
                "bot_id TEXT PRIMARY KEY, symbol TEXT, strategy TEXT, status TEXT, "
                "created_at TEXT, last_signal TEXT, last_action_ms INTEGER NOT NULL DEFAULT 0)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS paper_bot_allocations ("
                "bot_id TEXT PRIMARY KEY REFERENCES paper_bots(bot_id), notional TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS paper_bot_commands ("
                "command_id TEXT PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS paper_control_requests ("
                "timestamp INTEGER, operation TEXT, attempt INTEGER, request TEXT, response TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS paper_bot_risk ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT OR IGNORE INTO paper_bots "
                "(bot_id, symbol, strategy, status, created_at, last_signal) VALUES (?,?,?,?,?,?)",
                [
                    (r["bot_id"], r["symbol"], r["strategy"], r["status"], r["created_at"], None)
                    for r in rows
                ],
            )

    def _bot(self, bot_id: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM paper_bots WHERE bot_id=?", (bot_id,)
        ).fetchone()
        if row is None:
            raise ControlError("unknown_bot")
        return dict(
            zip(
                (
                    "bot_id",
                    "symbol",
                    "strategy",
                    "status",
                    "created_at",
                    "last_signal",
                    "last_action_ms",
                ),
                row,
                strict=True,
            )
        )

    def _apply(
        self, operation: str, bot_id: str, signal_id: str, notional: Decimal
    ) -> dict[str, object]:
        if operation == "positions":
            rows = self.connection.execute(
                "SELECT a.bot_id, b.symbol, a.notional FROM "
                "paper_bot_allocations a JOIN paper_bots b USING(bot_id)"
            ).fetchall()
            return {
                "result": [
                    {"bot_id": b, "symbol": s, "notional": n, "kind": "paper_capital_reservation"}
                    for b, s, n in rows
                ],
                "paper_only": True,
                "live_execution": False,
            }
        if operation == "orders":
            return {"result": [], "paper_only": True, "live_execution": False}
        bot = self._bot(bot_id)
        current = bot["status"]
        desired = {
            "start": "RUNNING",
            "stop": "STOPPED",
            "pause": "PAUSED",
            "resume": "RUNNING",
            "close": "STOPPED",
        }.get(operation)
        allowed = {
            "start": ("STOPPED",),
            "pause": ("RUNNING",),
            "resume": ("PAUSED",),
            "stop": ("RUNNING", "PAUSED", "STOPPED"),
            "close": ("RUNNING", "PAUSED", "STOPPED"),
        }
        if operation == "status":
            return bot | {"paper_only": True, "live_execution": False}
        if desired is None or current not in allowed[operation]:
            raise ControlError("invalid_bot_state")
        if operation == "start":
            money(notional, positive=True)
            if self.connection.execute(
                "SELECT 1 FROM paper_bot_allocations WHERE bot_id=?", (bot_id,)
            ).fetchone():
                raise ControlError("position_overlap")
            self.connection.execute(
                "INSERT INTO paper_bot_allocations VALUES (?, ?)", (bot_id, str(notional))
            )
        if operation == "close":
            self.connection.execute("DELETE FROM paper_bot_allocations WHERE bot_id=?", (bot_id,))
        self.connection.execute(
            "UPDATE paper_bots SET status=?, last_signal=?, " "last_action_ms=? WHERE bot_id=?",
            (desired, signal_id, self.clock(), bot_id),
        )
        return {
            "code": "0",
            "bot_id": bot_id,
            "status": desired,
            "paper_only": True,
            "live_execution": False,
            "simulated": True,
            "operation": operation,
        }

    def _request(
        self, operation: str, bot_id: str, *, signal_id: str = "", notional: Decimal = Decimal(0)
    ) -> dict[str, object]:
        money(notional)
        read_only = operation in ("status", "positions", "orders")
        if not read_only and not signal_id.strip():
            raise ControlError("signal_id_required")
        request = json.dumps(
            {
                "bot_id": bot_id,
                "operation": operation,
                "signal_id": signal_id,
                "notional": str(notional),
            },
            sort_keys=True,
        )
        command_id = json.dumps([bot_id, operation, signal_id])
        with atomic(self.connection):
            cached = self.connection.execute(
                "SELECT request, response FROM paper_bot_commands " "WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if cached:
                if cached[0] != request:
                    raise ControlError("idempotency_conflict")
                return cast(dict[str, object], json.loads(cached[1]))
            for attempt in range(3):
                self.connection.execute("SAVEPOINT bot_attempt")
                try:
                    response = self._apply(operation, bot_id, signal_id, notional)
                except (TimeoutError, ConnectionError, sqlite3.OperationalError):
                    self.connection.execute("ROLLBACK TO bot_attempt")
                    self.connection.execute("RELEASE bot_attempt")
                    self._log(operation, attempt, request, {"error": "transient_failure"})
                    if attempt == 2:
                        raise ControlError("retry_exhausted") from None
                    self.sleep(2**attempt)
                    continue
                except ControlError as exc:
                    self.connection.execute("ROLLBACK TO bot_attempt")
                    self.connection.execute("RELEASE bot_attempt")
                    self._log(operation, attempt, request, {"error": str(exc)})
                    raise
                self.connection.execute("RELEASE bot_attempt")
                self._log(operation, attempt, request, response)
                if not read_only:
                    self.connection.execute(
                        "INSERT INTO paper_bot_commands VALUES (?,?,?)",
                        (command_id, request, json.dumps(response)),
                    )
                return response
        raise AssertionError("Unreachable retry state")  # pragma: no cover

    def _log(self, operation: str, attempt: int, request: str, response: dict[str, object]) -> None:
        LOG.info(
            "paper_okx_request",
            extra={
                "operation": operation,
                "attempt": attempt,
                "request": request,
                "response": response,
            },
        )
        self.connection.execute(
            "INSERT INTO paper_control_requests VALUES (?,?,?,?,?)",
            (self.clock(), operation, attempt, request, json.dumps(response)),
        )

    def start_bot(self, bot_id: str, *, signal_id: str, notional: Decimal) -> dict[str, object]:
        return self._request("start", bot_id, signal_id=signal_id, notional=notional)

    def stop_bot(self, bot_id: str, *, signal_id: str) -> dict[str, object]:
        return self._request("stop", bot_id, signal_id=signal_id)

    def pause_bot(self, bot_id: str, *, signal_id: str) -> dict[str, object]:
        return self._request("pause", bot_id, signal_id=signal_id)

    def resume_bot(self, bot_id: str, *, signal_id: str) -> dict[str, object]:
        return self._request("resume", bot_id, signal_id=signal_id)

    def close_position_if_supported(self, bot_id: str, *, signal_id: str) -> dict[str, object]:
        return self._request("close", bot_id, signal_id=signal_id)

    def get_bot_status(self, bot_id: str) -> dict[str, object]:
        return self._request("status", bot_id)

    def get_positions(self) -> list[dict[str, object]]:
        return cast(list[dict[str, object]], self._request("positions", "")["result"])

    def get_orders(self) -> list[dict[str, object]]:
        # Bot lifecycle simulation reserves capital; it does not fabricate exchange fills.
        return cast(list[dict[str, object]], self._request("orders", "")["result"])

    def set_drawdown(self, value: Decimal) -> None:
        money(value)
        if value > 1:
            raise ValueError("Drawdown exceeds one")
        with atomic(self.connection):
            self.connection.execute(
                "INSERT OR REPLACE INTO paper_bot_risk VALUES (?,?)", ("drawdown", str(value))
            )
