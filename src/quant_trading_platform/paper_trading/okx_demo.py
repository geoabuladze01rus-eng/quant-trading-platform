"""Isolated OKX exchange-demo execution; never imported by the paper API.

An intent is reserved before network I/O. Uncertain submissions are never retried.
This ledger is an execution journal, not the local paper accounting database.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import time
from typing import Any
from urllib.parse import urlencode

import httpx

from quant_trading_platform.paper_trading.models import decimal_text

HOST = "https://openapi.okx.com"
PAIRS = frozenset({"BTC-USDT", "ETH-USDT", "SOL-USDT"})


@dataclass(frozen=True)
class DemoCredentials:
    key: str = field(repr=False)
    secret: str = field(repr=False)
    passphrase: str = field(repr=False)

    def __post_init__(self) -> None:
        if not all((self.key, self.secret, self.passphrase)):
            raise ValueError("Нужны отдельные ключи демо-счёта OKX")


@dataclass(frozen=True)
class DemoIntent:
    signal_id: str
    symbol: str
    entry: Decimal
    quantity: Decimal
    stop: Decimal
    take_profit: Decimal
    expires_at_ms: int

    def payload(self, now_ms: int) -> dict[str, Any]:
        values = (self.entry, self.quantity, self.stop, self.take_profit)
        if (
            not self.signal_id
            or len(self.signal_id) > 128
            or self.symbol not in PAIRS
            or any(not v.is_finite() or v <= 0 for v in values)
            or not self.stop < self.entry < self.take_profit
            or not now_ms < self.expires_at_ms <= now_ms + 60_000
        ):
            raise ValueError("Некорректный или просроченный демо-сигнал")
        notional = self.entry * self.quantity
        # Hard pilot limits: $100 per order, $1 planned stop loss, >=2R after costs.
        loss = (self.entry - self.stop) * self.quantity + notional * Decimal("0.003")
        gain = (self.take_profit - self.entry) * self.quantity - notional * Decimal("0.003")
        if notional > 100 or loss > 1 or gain < loss * 2:
            raise ValueError("Демо-сигнал не прошёл лимиты риска и затрат")
        client_id = "demo" + hashlib.sha256(self.signal_id.encode()).hexdigest()[:28]
        return {
            "instId": self.symbol,
            "tdMode": "cash",
            "side": "buy",
            "ordType": "limit",
            "px": decimal_text(self.entry),
            "sz": decimal_text(self.quantity),
            "clOrdId": client_id,
            "attachAlgoOrds": [
                {
                    "attachAlgoClOrdId": client_id,
                    "slTriggerPx": decimal_text(self.stop),
                    "slOrdPx": "-1",
                    "tpTriggerPx": decimal_text(self.take_profit),
                    "tpOrdPx": "-1",
                }
            ],
        }


class OKXDemoBot:
    """Cash-only pilot, maximum $200 reserved for its lifetime.

    Restart with the same journal. Reservations (including uncertain/rejected
    requests) are retained conservatively. No automatic budget reset or retries.
    """

    def __init__(
        self,
        credentials: DemoCredentials,
        journal: Path,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], int] = lambda: int(time() * 1000),
    ) -> None:
        journal.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(journal)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS intents "
            "(signal_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
            "notional TEXT NOT NULL, result TEXT)"
        )
        self.db.commit()
        self.credentials = credentials
        self.clock = clock
        self.http = httpx.Client(
            base_url=HOST, transport=transport, timeout=10, follow_redirects=False
        )

    def close(self) -> None:
        self.http.close()
        self.db.close()

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        expires_at_ms: int | None = None,
    ) -> dict[str, Any]:
        body = "" if payload is None else json.dumps(payload, separators=(",", ":"))
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        signature = base64.b64encode(
            hmac.new(
                self.credentials.secret.encode(),
                (timestamp + method + path + body).encode(),
                hashlib.sha256,
            ).digest()
        ).decode()
        headers = {
            "OK-ACCESS-KEY": self.credentials.key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-PASSPHRASE": self.credentials.passphrase,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "x-simulated-trading": "1",
            "Content-Type": "application/json",
        }
        if expires_at_ms is not None:
            headers["expTime"] = str(expires_at_ms)
        try:
            response = self.http.request(method, path, content=body, headers=headers)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("code") != "0":
                raise ValueError("Демо-запрос отклонён OKX")
            return data
        except (httpx.HTTPError, ValueError):
            # Do not propagate exchange response bodies or authenticated requests.
            raise RuntimeError("Демо-запрос не подтверждён; проверьте журнал и OKX") from None

    def check_connection(self) -> dict[str, Any]:
        """Authenticated demo balance read; never submits an order."""
        return self._request("GET", "/api/v5/account/balance?ccy=USDT")

    def submit(self, intent: DemoIntent) -> dict[str, Any]:
        payload = intent.payload(self.clock())
        encoded = json.dumps(payload, sort_keys=True)
        # Acquire a write lock before reserving the signal and lifetime budget.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT payload, result FROM intents WHERE signal_id=?", (intent.signal_id,)
            ).fetchone()
            if row:
                if row[0] != encoded:
                    raise ValueError("Повторный signal_id с другим содержимым")
                self.db.commit()
                return {
                    "status": "already_reserved",
                    "result": json.loads(row[1]) if row[1] else None,
                }
            if self.db.execute("SELECT 1 FROM intents WHERE result IS NULL LIMIT 1").fetchone():
                raise RuntimeError("Неподтверждённый ордер блокирует новые демо-заявки")
            total = sum(
                (Decimal(r[0]) for r in self.db.execute("SELECT notional FROM intents")), Decimal(0)
            )
            if total + intent.entry * intent.quantity > 200:
                raise ValueError("Исчерпан бюджет демо-пилота")
            self.db.execute(
                "INSERT INTO intents VALUES (?, ?, ?, NULL)",
                (intent.signal_id, encoded, decimal_text(intent.entry * intent.quantity)),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        # Reservation may have waited for a SQLite lock; reject an expired intent.
        intent.payload(self.clock())
        result = self._request("POST", "/api/v5/trade/order", payload, intent.expires_at_ms)
        rows = result.get("data", [])
        if (
            not isinstance(rows, list)
            or not rows
            or not isinstance(rows[0], dict)
            or rows[0].get("sCode") != "0"
            or not rows[0].get("ordId")
        ):
            raise RuntimeError("Демо-ордер отклонён; повторная отправка заблокирована")
        # Persist only public order identifiers, not response/error text or credentials.
        receipt = {"ordId": rows[0]["ordId"], "clOrdId": payload["clOrdId"]}
        self.db.execute(
            "UPDATE intents SET result=? WHERE signal_id=?", (json.dumps(receipt), intent.signal_id)
        )
        self.db.commit()
        return {"status": "accepted", "receipt": receipt, "filled": False, "demo_only": True}

    def order_status(self, signal_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT payload FROM intents WHERE signal_id=?", (signal_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Демо-сигнал не найден")
        payload = json.loads(row[0])
        query = urlencode({"instId": payload["instId"], "clOrdId": payload["clOrdId"]})
        return self._request("GET", "/api/v5/trade/order?" + query)
