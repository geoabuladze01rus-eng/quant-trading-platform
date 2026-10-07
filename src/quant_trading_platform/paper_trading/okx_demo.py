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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import time
from typing import Any
from urllib.parse import urlencode

import httpx

from quant_trading_platform.paper_trading.models import decimal_text

HOST = "https://openapi.okx.com"
PAIRS = frozenset({"BTC-USDT", "ETH-USDT", "SOL-USDT"})


@dataclass(frozen=True)
class ConfirmedDemoSignal:
    """Decision supplied by the strategy; does not fabricate missing evidence."""

    intent: DemoIntent
    confidence: str
    confirmations: frozenset[str]
    observed_at_ms: int
    news_checked_at_ms: int
    higher_timeframes_aligned: bool
    news_blocked: bool

    def validate(self, now_ms: int) -> None:
        allowed = {"trend_4h", "structure_1h", "level_retest", "volume", "liquidity", "vwap"}
        if (
            self.confidence not in {"HIGH", "VERY HIGH"}
            or not self.higher_timeframes_aligned
            or self.news_blocked
            or not 0 <= now_ms - self.observed_at_ms <= 15_000
            or not 0 <= now_ms - self.news_checked_at_ms <= 300_000
            or not self.confirmations <= allowed
            or len(self.confirmations) < 4
            or not {"trend_4h", "structure_1h"} <= self.confirmations
        ):
            raise ValueError("Нет подтверждённого сигнала по методике Crypto Signal Watch")
        self.intent.payload(now_ms)


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
            "ordType": "fok",
            "stpMode": "cancel_maker",
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
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS lifecycle "
            "(signal_id TEXT PRIMARY KEY, expires_at_ms INTEGER NOT NULL, "
            "state TEXT NOT NULL, cancel_reserved INTEGER NOT NULL DEFAULT 0, "
            "snapshot TEXT NOT NULL DEFAULT '{}')"
        )
        self.db.execute(
            "INSERT OR IGNORE INTO lifecycle (signal_id, expires_at_ms, state) "
            "SELECT signal_id, 0, 'legacy_needs_reconciliation' FROM intents"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS demo_identity "
            "(id INTEGER PRIMARY KEY CHECK(id=1), account_hash TEXT NOT NULL)"
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

    def execute_signal(self, signal: ConfirmedDemoSignal) -> dict[str, Any]:
        signal.validate(self.clock())
        return self.submit(signal.intent, validate_signal=lambda: signal.validate(self.clock()))

    @staticmethod
    def _rows(response: dict[str, Any]) -> list[dict[str, Any]]:
        rows = response.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError("Некорректные данные демо-счёта")
        return rows

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            result = Decimal(str(value))
            if not result.is_finite():
                raise ValueError
            return result
        except (InvalidOperation, ValueError):
            raise RuntimeError("Некорректное число в ответе OKX") from None

    def _preflight(self, intent: DemoIntent) -> None:
        self._bind_account()
        query = urlencode({"instType": "SPOT", "instId": intent.symbol})
        rows = self._rows(self._request("GET", "/api/v5/account/instruments?" + query))
        if len(rows) != 1:
            raise RuntimeError("Инструмент недоступен на демо-счёте")
        rule = rows[0]
        if (
            rule.get("instId") != intent.symbol
            or rule.get("instType") != "SPOT"
            or rule.get("state") != "live"
            or rule.get("quoteCcy") != "USDT"
        ):
            raise RuntimeError("Торговля этим инструментом запрещена")
        tick, lot, minimum = (self._decimal(rule.get(k)) for k in ("tickSz", "lotSz", "minSz"))
        if (
            min(tick, lot, minimum) <= 0
            or intent.quantity < minimum
            or intent.quantity % lot != 0
            or any(price % tick != 0 for price in (intent.entry, intent.stop, intent.take_profit))
        ):
            raise ValueError("Цена или размер не соответствуют правилам OKX")
        fees = self._rows(self._request("GET", "/api/v5/account/trade-fee?" + query))
        if len(fees) != 1 or max(
            abs(self._decimal(fees[0].get("maker"))),
            abs(self._decimal(fees[0].get("taker"))),
        ) > Decimal("0.0015"):
            raise RuntimeError("Комиссия превышает резерв затрат пилота")
        balance = self._rows(self.check_connection())
        if len(balance) != 1:
            raise RuntimeError("Не удалось проверить капитал демо-счёта")
        equity = self._decimal(balance[0].get("totalEq"))
        planned_loss = (intent.entry - intent.stop) * intent.quantity + (
            intent.entry * intent.quantity * Decimal("0.003")
        )
        if equity <= 0 or planned_loss > equity * Decimal("0.005"):
            raise RuntimeError("Расчётный риск превышает 0,5% капитала")
        details = balance[0].get("details") if len(balance) == 1 else None
        if not isinstance(details, list) or any(not isinstance(r, dict) for r in details):
            raise RuntimeError("Не удалось проверить доступный баланс")
        usd = [r for r in details if r.get("ccy") == "USDT"]
        if len(usd) != 1 or self._decimal(usd[0].get("availBal")) < (
            intent.entry * intent.quantity * Decimal("1.003")
        ):
            raise RuntimeError("Недостаточно свободных USDT для демо-входа")

    def _bind_account(self) -> None:
        rows = self._rows(self._request("GET", "/api/v5/account/config"))
        if len(rows) != 1 or not isinstance(rows[0].get("uid"), str) or not rows[0]["uid"]:
            raise RuntimeError("Не удалось определить демо-счёт")
        identity = hashlib.sha256(rows[0]["uid"].encode()).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT OR IGNORE INTO demo_identity VALUES (1, ?)", (identity,))
            stored = self.db.execute("SELECT account_hash FROM demo_identity WHERE id=1").fetchone()
            if stored is None or stored[0] != identity:
                raise RuntimeError("Журнал принадлежит другому демо-счёту")
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def submit(
        self,
        intent: DemoIntent,
        *,
        validate_signal: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        payload = intent.payload(self.clock())
        encoded = json.dumps(payload, sort_keys=True)
        existing = self.db.execute(
            "SELECT payload, result FROM intents WHERE signal_id=?", (intent.signal_id,)
        ).fetchone()
        if existing:
            if existing[0] != encoded:
                raise ValueError("Повторный signal_id с другим содержимым")
            return {
                "status": "already_reserved",
                "result": json.loads(existing[1]) if existing[1] else None,
            }
        self._preflight(intent)
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
            if self.db.execute(
                "SELECT 1 FROM lifecycle WHERE state NOT IN ('canceled_empty', 'closed') LIMIT 1"
            ).fetchone():
                raise RuntimeError("Незавершённая демо-позиция блокирует новый вход")
            realized = sum(
                (
                    self._decimal(json.loads(row[0]).get("realized_pnl_usdt"))
                    for row in self.db.execute(
                        "SELECT snapshot FROM lifecycle WHERE state='closed'"
                    )
                ),
                Decimal(0),
            )
            if realized <= -2:
                raise RuntimeError("Достигнут предел фактического убытка демо-пилота")
            total = sum(
                (Decimal(r[0]) for r in self.db.execute("SELECT notional FROM intents")), Decimal(0)
            )
            if total + intent.entry * intent.quantity > 200:
                raise ValueError("Исчерпан бюджет демо-пилота")
            self.db.execute(
                "INSERT INTO intents VALUES (?, ?, ?, NULL)",
                (intent.signal_id, encoded, decimal_text(intent.entry * intent.quantity)),
            )
            self.db.execute(
                "INSERT INTO lifecycle (signal_id, expires_at_ms, state) VALUES (?, ?, ?)",
                (intent.signal_id, intent.expires_at_ms, "submission_unknown"),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        # Reservation may have waited for a SQLite lock; reject an expired intent.
        intent.payload(self.clock())
        if validate_signal is not None:
            validate_signal()
        result = self._request("POST", "/api/v5/trade/order", payload, intent.expires_at_ms)
        rows = result.get("data", [])
        if (
            not isinstance(rows, list)
            or not rows
            or not isinstance(rows[0], dict)
            or rows[0].get("sCode") != "0"
            or not rows[0].get("ordId")
            or rows[0].get("clOrdId", payload["clOrdId"]) != payload["clOrdId"]
        ):
            raise RuntimeError("Демо-ордер отклонён; повторная отправка заблокирована")
        # Persist only public order identifiers, not response/error text or credentials.
        receipt = {"ordId": rows[0]["ordId"], "clOrdId": payload["clOrdId"]}
        self.db.execute(
            "UPDATE intents SET result=? WHERE signal_id=?", (json.dumps(receipt), intent.signal_id)
        )
        self.db.execute(
            "UPDATE lifecycle SET state='accepted' WHERE signal_id=?", (intent.signal_id,)
        )
        self.db.commit()
        return {"status": "accepted", "receipt": receipt, "filled": False, "demo_only": True}

    def order_status(self, signal_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT payload, result FROM intents WHERE signal_id=?", (signal_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Демо-сигнал не найден")
        payload = json.loads(row[0])
        query = urlencode({"instId": payload["instId"], "clOrdId": payload["clOrdId"]})
        return self._request("GET", "/api/v5/trade/order?" + query)

    def _save_state(self, signal_id: str, state: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        snapshot = {
            "signal_id": signal_id,
            "state": state,
            **snapshot,
            "checked_at_ms": self.clock(),
            "demo_only": True,
        }
        self.db.execute(
            "UPDATE lifecycle SET state=?, snapshot=? WHERE signal_id=?",
            (state, json.dumps(snapshot), signal_id),
        )
        self.db.commit()
        return snapshot

    def monitor(self, signal_id: str) -> dict[str, Any]:
        """Reconcile one known order; cancel stale entries once, verify protection.

        Does not resubmit an uncertain entry or assume cancellation from an ACK.
        An effective algo remains blocked until its child execution is reconciled.
        """
        row = self.db.execute(
            "SELECT payload, result FROM intents WHERE signal_id=?", (signal_id,)
        ).fetchone()
        meta = self.db.execute(
            "SELECT expires_at_ms, cancel_reserved FROM lifecycle WHERE signal_id=?", (signal_id,)
        ).fetchone()
        if row is None or meta is None:
            raise RuntimeError("Нет данных восстановления; требуется проверка оператора")
        self._bind_account()
        payload = json.loads(row[0])
        try:
            orders = self._rows(self.order_status(signal_id))
            if len(orders) != 1:
                raise RuntimeError("Не удалось однозначно определить ордер")
            order = orders[0]
            recorded = json.loads(row[1]) if row[1] else None
            if (
                order.get("instId") != payload["instId"]
                or order.get("clOrdId") != payload["clOrdId"]
                or order.get("side") != "buy"
                or order.get("tdMode") != "cash"
                or order.get("instType") != "SPOT"
                or self._decimal(order.get("px")) != self._decimal(payload["px"])
                or not order.get("ordId")
                or (recorded is not None and order.get("ordId") != recorded["ordId"])
                or self._decimal(order.get("sz")) != self._decimal(payload["sz"])
            ):
                raise RuntimeError("Ордер не соответствует журналу")
            quantity = self._decimal(order.get("accFillSz"))
            if not 0 <= quantity <= self._decimal(payload["sz"]):
                raise RuntimeError("Некорректный объём исполнения")
            self.db.execute(
                "UPDATE intents SET result=? WHERE signal_id=? AND result IS NULL",
                (json.dumps({"ordId": order["ordId"], "clOrdId": payload["clOrdId"]}), signal_id),
            )
            self.db.commit()
            snapshot = {"ordId": order["ordId"], "filled_quantity": decimal_text(quantity)}
            state = order.get("state")
            if state in {"live", "partially_filled"}:
                stale = self.clock() >= meta[0]
                if (stale or quantity > 0) and not meta[1]:
                    # Atomic once-only cancellation claim across competing monitors.
                    claim = self.db.execute(
                        "UPDATE lifecycle SET cancel_reserved=1 WHERE signal_id=? "
                        "AND cancel_reserved=0",
                        (signal_id,),
                    ).rowcount
                    self.db.commit()
                    if claim:
                        result = self._rows(
                            self._request(
                                "POST",
                                "/api/v5/trade/cancel-order",
                                {
                                    "instId": payload["instId"],
                                    "clOrdId": payload["clOrdId"],
                                },
                            )
                        )
                        if len(result) != 1 or result[0].get("sCode") != "0":
                            raise RuntimeError("Отмена не подтверждена")
                label = (
                    "unprotected_partial"
                    if quantity > 0
                    else ("cancel_pending" if stale else "entry_pending")
                )
                return self._save_state(signal_id, label, snapshot)
            if state in {"canceled", "mmp_canceled"}:
                return self._save_state(
                    signal_id,
                    "canceled_empty" if quantity == 0 else "unprotected_partial",
                    snapshot,
                )
            if state != "filled" or quantity != self._decimal(payload["sz"]):
                raise RuntimeError("Неизвестное состояние ордера")
            entry_price = self._decimal(order.get("avgPx"))
            entry_fee = self._decimal(order.get("fee"))
            base_ccy = payload["instId"].split("-")[0]
            if order.get("feeCcy") == base_ccy and entry_fee > 0:
                raise RuntimeError("Rebate в базовом активе требует сверки остатка")
            if not 0 < entry_price <= self._decimal(payload["px"]) or order.get("feeCcy") not in {
                "USDT",
                base_ccy,
            }:
                raise RuntimeError("Не подтверждены цена исполнения и валюта комиссии")
            net_quantity = (
                quantity + min(entry_fee, Decimal(0)) if (order["feeCcy"] == base_ccy) else quantity
            )
            if net_quantity <= 0:
                raise RuntimeError("Не подтверждён остаток позиции после комиссии")
            snapshot["net_quantity"] = decimal_text(net_quantity)
            query = urlencode({"algoClOrdId": payload["clOrdId"]})
            algos = self._rows(self._request("GET", "/api/v5/trade/order-algo?" + query))
            if len(algos) != 1:
                raise RuntimeError("Защита позиции не подтверждена")
            algo = algos[0]
            attached = payload["attachAlgoOrds"][0]
            if (
                algo.get("instId") != payload["instId"]
                or algo.get("algoClOrdId") != payload["clOrdId"]
                or algo.get("side") != "sell"
                or algo.get("tdMode") != "cash"
                or not algo.get("algoId")
                or self._decimal(algo.get("sz")) != net_quantity
                or any(
                    self._decimal(algo.get(k)) != self._decimal(attached[k])
                    for k in ("slTriggerPx", "slOrdPx", "tpTriggerPx", "tpOrdPx")
                )
            ):
                raise RuntimeError("Защитный ордер не соответствует позиции")
            snapshot["algoId"] = algo["algoId"]
            if algo.get("state") == "live":
                return self._save_state(signal_id, "protected", snapshot)
            if algo.get("state") == "effective":
                children = algo.get("ordIdList")
                if not isinstance(children, list) or len(children) != 1 or not children[0]:
                    raise RuntimeError("Не определён ордер выхода")
                query = urlencode({"instId": payload["instId"], "ordId": children[0]})
                exits = self._rows(self._request("GET", "/api/v5/trade/order?" + query))
                if len(exits) != 1:
                    raise RuntimeError("Выход не подтверждён")
                exit_order = exits[0]
                if (
                    exit_order.get("ordId") != children[0]
                    or exit_order.get("instId") != payload["instId"]
                    or exit_order.get("side") != "sell"
                    or exit_order.get("tdMode") != "cash"
                    or exit_order.get("instType") != "SPOT"
                    or self._decimal(exit_order.get("sz")) != net_quantity
                ):
                    raise RuntimeError("Выход не соответствует позиции")
                if (
                    exit_order.get("state") == "filled"
                    and self._decimal(exit_order.get("accFillSz")) == net_quantity
                ):
                    exit_price = self._decimal(exit_order.get("avgPx"))
                    exit_fee = self._decimal(exit_order.get("fee"))
                    if exit_price <= 0 or exit_order.get("feeCcy") != "USDT":
                        raise RuntimeError("Не подтверждены цена и комиссия выхода")
                    quote_entry_fee = entry_fee if order["feeCcy"] == "USDT" else Decimal(0)
                    pnl = (
                        exit_price * net_quantity
                        + exit_fee
                        - (entry_price * quantity - quote_entry_fee)
                    )
                    snapshot["realized_pnl_usdt"] = decimal_text(pnl)
                    snapshot["exit_ordId"] = children[0]
                    return self._save_state(signal_id, "closed", snapshot)
            return self._save_state(signal_id, "exit_needs_reconciliation", snapshot)
        except (RuntimeError, ValueError):
            return self._save_state(signal_id, "reconciliation_required", {})

    def recover(self) -> list[dict[str, Any]]:
        """Poll durable known intents after restart. Never recreate an entry."""
        ids = [row[0] for row in self.db.execute("SELECT signal_id FROM intents")]
        return [self.monitor(signal_id) for signal_id in ids]
