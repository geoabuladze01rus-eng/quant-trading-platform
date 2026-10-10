"""Isolated OKX demo Signal Bot adapter; never used by the spot paper engine.

Default is preview only. A receipt acknowledges a webhook, NOT an exchange fill.
POST is at-most-once locally: ambiguous outcomes require manual reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import httpx

from quant_trading_platform.execution_engine import Signal
from quant_trading_platform.okx_controller.manager import ControlError, atomic, money

DEMO_ENDPOINT = "https://www.okx.com/pap/algo/signal/trigger"
Action = Literal["ENTER_LONG", "EXIT_LONG"]


@dataclass(frozen=True)
class DemoSignalConfig:
    token: str = field(repr=False)
    enabled: bool = False
    demo_bot_verified: bool = False
    margin_usdt: Decimal = Decimal("10")

    def __post_init__(self) -> None:
        money(self.margin_usdt, positive=True)
        if self.margin_usdt > Decimal("10"):
            raise ControlError("demo_margin_limit")
        if not self.token.strip() or any(c.isspace() for c in self.token):
            raise ControlError("invalid_demo_token")
        if "{{" in self.token or "}}" in self.token:
            raise ControlError("unresolved_demo_token")


def preview(signal: Signal, config: DemoSignalConfig, *, now_ms: int) -> dict[str, str]:
    """Validate an entry and return a redacted payload, safe for logs and previews."""
    if signal.symbol != "BTC/USDT" or signal.direction != "LONG":
        raise ControlError("demo_btc_long_only")
    if not 0 <= now_ms - signal.timestamp_ms <= 1000:
        raise ControlError("stale_signal")
    if signal.valid_until_ms is not None and now_ms > signal.valid_until_ms:
        raise ControlError("stale_signal")
    if not (
        signal.confidence == "HIGH"
        and Decimal("72") <= signal.score < Decimal("84")
        or signal.confidence == "VERY_HIGH"
        and signal.score >= Decimal("84")
    ):
        raise ControlError("confidence")
    if signal.rr < 2:
        raise ControlError("rr")
    if signal.market_state not in ("range", "trend"):
        raise ControlError("market_state")
    if not 0 < signal.atr_pct <= Decimal(".03") or signal.volatility > Decimal(".05"):
        raise ControlError("volatility")
    if signal.notional_usd != config.margin_usdt or not (
        0 < signal.risk_usd <= config.margin_usdt * Decimal(".01")
    ):
        raise ControlError("demo_size_or_risk")
    return {
        "action": "ENTER_LONG",
        "instrument": "BTC-USDT-SWAP",
        "signalToken": "[REDACTED]",
        "timestamp": datetime.fromtimestamp(signal.timestamp_ms / 1000, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "maxLag": "60",
        "orderType": "market",
        "investmentType": "margin",
        "amount": format(config.margin_usdt, "f"),
    }


class DemoSignalSender:
    """One isolated bot per ledger. Never shares a token across multiple bots.

    The slot deliberately stays locked even after an HTTP acknowledgment. A
    future exchange-state reconciler must confirm closure before another entry.
    No automatic retry, no redirect, no live URL option, no response-body logging.
    """

    def __init__(self, config: DemoSignalConfig, connection: sqlite3.Connection) -> None:
        self.config, self.connection = config, connection
        with atomic(connection):
            connection.execute(
                "CREATE TABLE IF NOT EXISTS okx_demo_dispatch ("
                "signal_id TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS okx_demo_slot ("
                "slot INTEGER PRIMARY KEY CHECK(slot=1), signal_id TEXT NOT NULL)"
            )

    def send_entry(self, signal: Signal, *, now_ms: int) -> str:
        payload = preview(signal, self.config, now_ms=now_ms)
        if not self.config.enabled:
            return "PREVIEW_ONLY"
        if not self.config.demo_bot_verified:
            raise ControlError("demo_bot_not_verified")
        if self.connection.in_transaction:
            raise ControlError("demo_requires_durable_reservation")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with atomic(self.connection):
            row = self.connection.execute(
                "SELECT digest, state FROM okx_demo_dispatch WHERE signal_id=?",
                (signal.signal_id,),
            ).fetchone()
            if row:
                if row[0] != digest:
                    raise ControlError("idempotency_conflict")
                return str(row[1])
            if self.connection.execute("SELECT 1 FROM okx_demo_slot").fetchone():
                raise ControlError("demo_position_or_pending_dispatch")
            self.connection.execute("INSERT INTO okx_demo_slot VALUES (1, ?)", (signal.signal_id,))
            self.connection.execute(
                "INSERT INTO okx_demo_dispatch VALUES (?, ?, 'PENDING_RECONCILIATION')",
                (signal.signal_id, digest),
            )
        payload["signalToken"] = self.config.token
        state = "PENDING_RECONCILIATION"
        try:
            # Use one POST only. A timeout could mean OKX already received it.
            with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
                response = client.post(DEMO_ENDPOINT, json=payload)
                if response.is_success:
                    state = "HTTP_ACK_REQUIRES_RECONCILIATION"
        except httpx.HTTPError:
            pass
        finally:
            payload["signalToken"] = "[REDACTED]"
        with atomic(self.connection):
            self.connection.execute(
                "UPDATE okx_demo_dispatch SET state=? WHERE signal_id=?",
                (state, signal.signal_id),
            )
        return state
