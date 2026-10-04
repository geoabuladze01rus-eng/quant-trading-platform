"""Durable daily-trend paper execution. Only public data and local SQLite writes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from time import time
from typing import Any, Protocol

from quant_trading_platform.config import MarketScope, Settings, TradingMode
from quant_trading_platform.explainability.reasons import human_reason
from quant_trading_platform.market_data.models import NormalizedOrderBook
from quant_trading_platform.models import Venue
from quant_trading_platform.paper_trading.models import PaperCommandError, decimal_text
from quant_trading_platform.paper_trading.okx_spot import (
    FEE_RATE_PCT,
    SLIPPAGE_RATE_PCT,
    OKXSpotPaperService,
    checked_book,
    sell_value,
)
from quant_trading_platform.paper_trading.reconciliation import reconcile_records
from quant_trading_platform.paper_trading.service import _plain
from quant_trading_platform.strategies.spot_momentum import (
    SYMBOLS,
    DailyCandle,
    trend_signal,
    validate_candles,
)

VERSION = "daily-trend-paper-v1"
logger = logging.getLogger(__name__)


class CandleProvider(Protocol):
    def completed_series(self, *, now_ms: int) -> dict[str, tuple[DailyCandle, ...]]: ...


class BookProvider(Protocol):
    def book_for_simulation(self, venue: Venue, symbol: str) -> NormalizedOrderBook | None: ...


class AutomaticSpotPaper:
    def __init__(
        self,
        service: OKXSpotPaperService,
        settings: Settings,
        candles: CandleProvider | None,
        books: BookProvider | None,
        clock: Callable[[], int] = lambda: int(time() * 1000),
    ) -> None:
        self.service, self.settings = service, settings
        self.candles, self.books, self.clock = candles, books, clock
        self.account_id = settings.okx_spot_paper_account_id
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._failure: str | None = None

    def _save(self, account: dict[str, Any], state: dict[str, Any], conn: Any) -> None:
        previous = account.get("auto_paper", {})
        # Heartbeats update the account, while audit only records changed decisions.
        decisions = [
            tuple(
                decision.get(key)
                for key in (
                    "symbol",
                    "action",
                    "signal_timestamp_ms",
                    "signal_reason_code",
                    "signal_close_usdt",
                    "signal_reference_usdt",
                    "side",
                    "reason_code",
                    "order_id",
                )
            )
            for decision in state.get("decisions", [])
        ]
        signature = sha256(
            repr((state["status"], state["reason_code"], decisions)).encode()
        ).hexdigest()
        state["decision_signature"] = signature
        state["human_reason"] = human_reason(state["reason_code"])
        account["auto_paper"] = state
        self.service.store.upsert_account(self.account_id, account, conn=conn)
        if previous.get("decision_signature") != signature:
            self.service.store.insert_audit(
                {
                    "account_id": self.account_id,
                    "actor": "automatic_paper",
                    "event_type": "auto_paper_cycle",
                    "reason_code": state["reason_code"],
                    "human_reason": state["human_reason"],
                    "details": state,
                    "correlation_id": f"{VERSION}:{state['checked_at_ms']}",
                },
                conn=conn,
            )
            logger.info(
                "auto_paper_cycle status=%s reason=%s", state["status"], state["reason_code"]
            )

    def _blocked(self, reason: str, *, halt: bool = False) -> dict[str, Any]:
        with self.service.store.transaction() as conn:
            account = self.service._account(self.account_id, conn)
            if halt:
                account["status"] = "halted"
            state = {
                **account.get("auto_paper", {}),
                "status": "blocked",
                "reason_code": reason,
                "checked_at_ms": self.clock(),
                "decisions": [],
            }
            self._save(account, state, conn)
        return state

    def cycle(self) -> dict[str, Any]:
        """A cycle commits fills, cursor, equity baseline and decision audit together."""
        if self._failure:
            return self._blocked(self._failure, halt=True)
        cfg = self.settings
        if not cfg.okx_spot_auto_enabled:
            return self._blocked("auto_paper_disabled")
        with self.service.store.transaction() as conn:
            if self.service._account(self.account_id, conn).get("auto_operator_paused"):
                return self._paused_state(conn)
        if (
            cfg.trading_mode != TradingMode.PAPER
            or cfg.live_trading_enabled
            or cfg.live_order_acceptance_gate
        ):
            return self._blocked("live_trading_locked")
        if (
            not cfg.public_market_data_enabled
            or cfg.market_scope == MarketScope.RUSSIAN_STOCKS
            or self.candles is None
            or self.books is None
        ):
            return self._blocked("source_unavailable")
        now = self.clock()
        try:
            series = self.candles.completed_series(now_ms=now)
            if set(series) != set(SYMBOLS):
                raise ValueError("Missing pair")
            for rows in series.values():
                validate_candles(rows, now_ms=now)
            if len({rows[-1].timestamp_ms for rows in series.values()}) != 1:
                raise ValueError("Inconsistent candle dates")
        except (ValueError, OSError):
            return self._blocked("completed_candles_unavailable")
        try:
            books = {
                symbol: checked_book(
                    self.books.book_for_simulation(Venue.OKX, symbol), symbol, cfg, now
                )
                for symbol in SYMBOLS
            }
        except PaperCommandError as error:
            return self._blocked(error.reason_code)
        try:
            return self._execute(series, books, now)
        except Exception:
            # An unexpected storage/accounting failure rolls back the whole cycle.
            # Latch locally as well, even if persisting the halt itself fails.
            self._failure = "auto_paper_execution_error"
            return self._blocked(self._failure, halt=True)

    def _execute(
        self,
        series: dict[str, tuple[DailyCandle, ...]],
        books: dict[str, NormalizedOrderBook],
        now: int,
    ) -> dict[str, Any]:
        store, cfg = self.service.store, self.settings
        with store.transaction() as conn:
            account = self.service._account(self.account_id, conn)
            # Check again under the same write lock as execution: a pause that
            # commits while data is being collected must prevent all later fills.
            if account.get("auto_operator_paused"):
                return self._paused_state(conn)
            reconciliation = reconcile_records(
                account,
                store.list_balances(self.account_id, conn=conn),
                store.list_orders(self.account_id, conn=conn),
                store.list_fills(self.account_id, conn=conn),
                store.list_positions(self.account_id, conn=conn),
            )
            state: dict[str, Any] = {
                **account.get("auto_paper", {}),
                "checked_at_ms": now,
                "decisions": [],
            }
            if account.get("status") != "active" or reconciliation["status"] != "ok":
                account["status"] = "halted"
                state.update(status="blocked", reason_code="reconciliation_mismatch")
                self._save(account, state, conn)
                return state
            marks = {
                symbol.split("/")[0]: decimal_text(book.bids[0].price)
                for symbol, book in books.items()
            }
            account.update(
                marks=marks,
                marks_timestamp_ms=now,
                mark_sources={asset: "okx:public_order_book" for asset in marks},
            )
            store.upsert_account(self.account_id, account, conn=conn)
            equity = Decimal(self.service.account(self.account_id, conn=conn)["equity_usd"])
            initial = Decimal(account["initial_balances"]["USDT"])
            today = datetime.fromtimestamp(now / 1000, UTC).date().isoformat()
            if state.get("day_utc") != today:
                # Carry the previous observed equity across midnight, including overnight gaps.
                # After a data outage this is conservatively a multi-day loss baseline.
                baseline = Decimal(state.get("equity_usdt", decimal_text(initial)))
                state.update(
                    day_utc=today,
                    day_start_equity_usdt=decimal_text(baseline),
                    baseline_source="previous_observed_equity",
                    daily_loss_halted=False,
                )
            baseline = Decimal(state["day_start_equity_usdt"])
            drawdown = (
                max(Decimal(0), (baseline - equity) * 100 / baseline) if baseline > 0 else 100
            )
            if initial <= 0 or baseline <= 0 or drawdown >= cfg.max_daily_loss_pct:
                state["daily_loss_halted"] = True
            cursor = dict(account.get("auto_signal_cursor", {}))
            signals = [trend_signal(symbol, series[symbol]) for symbol in SYMBOLS]
            # Reductions precede entries. A daily-loss halt still permits funded exits.
            signals.sort(key=lambda signal: (signal.action != "exit_review", signal.symbol))
            decisions: list[dict[str, Any]] = []
            for signal in signals:
                symbol, stamp = signal.symbol, signal.candle_timestamp_ms
                base = symbol.split("/")[0]
                held = Decimal(account.get("spot_inventory", {}).get(base, {}).get("quantity", "0"))
                reason, side, order_id = "trend_unconfirmed", None, None
                quantity: Decimal | None = None
                amount = min(
                    cfg.max_trade_notional_usd,
                    initial
                    * cfg.okx_spot_max_asset_pct
                    / 100
                    / (1 + (FEE_RATE_PCT + SLIPPAGE_RATE_PCT) / 100),
                )
                if account.get("status") != "active":
                    reason = "reconciliation_mismatch"
                elif int(cursor.get(symbol, -1)) >= stamp:
                    reason = "signal_already_processed"
                elif signal.action == "exit_review" and held > 0:
                    side = "sell"
                    # Bound each sell by the command cap and observed depth. Preserve exact
                    # inventory quantity on the last chunk; no floating dust from quote sizing.
                    remaining, budget, quantity = held, cfg.max_trade_notional_usd, Decimal(0)
                    for level in books[symbol].bids:
                        take = min(remaining, level.quantity, budget / level.price)
                        if take * level.price > budget:
                            take = take.next_minus()
                        quantity += take
                        remaining -= take
                        budget -= take * level.price
                        if remaining == 0 or budget <= 0:
                            break
                    amount = sell_value(books[symbol].bids, quantity)
                elif signal.action == "entry_review":
                    if state["daily_loss_halted"]:
                        reason = "daily_loss_limit_reached"
                    elif held > 0:
                        reason = "position_already_open"
                    else:
                        side = "buy"
                        spread = (
                            books[symbol].asks[0].price / books[symbol].bids[0].price - 1
                        ) * 100
                        marked_assets = sum(
                            (
                                Decimal(item["quantity"]) * Decimal(marks[asset])
                                for asset, item in account.get("spot_inventory", {}).items()
                            ),
                            Decimal(0),
                        )
                        costs = amount * (FEE_RATE_PCT + SLIPPAGE_RATE_PCT) / 100
                        if spread > cfg.okx_spot_auto_max_spread_pct:
                            side, reason = None, "spread_limit_exceeded"
                        elif books[symbol].bids[0].price <= signal.reference:
                            side, reason = None, "trend_invalidated"
                        elif abs(books[symbol].asks[0].price / signal.close - 1) * 100 > (
                            cfg.okx_spot_auto_max_entry_deviation_pct
                        ):
                            side, reason = None, "entry_price_deviation"
                        elif (
                            marked_assets + amount + costs
                            > initial * cfg.okx_spot_max_total_pct / 100
                        ):
                            side, reason = None, "notional_limit_exceeded"
                if side is not None:
                    # Quantity-based exit chunks have distinct durable keys; entries get one
                    # key per pair/day. The cursor commits in the same transaction as the fill.
                    suffix = decimal_text(held) if side == "sell" else "entry"
                    digest = sha256(
                        f"{VERSION}:{symbol}:{stamp}:{side}:{suffix}".encode()
                    ).hexdigest()
                    key = f"auto:{digest}"
                    store.upsert_account(self.account_id, account, conn=conn)
                    conn.execute("SAVEPOINT auto_command")
                    try:
                        result = self.service.execute_spot(
                            symbol=symbol,
                            side=side,
                            notional_usdt=amount,
                            book=books[symbol],
                            settings=cfg,
                            idempotency_key=key,
                            account_id=self.account_id,
                            sell_quantity=quantity,
                            signal_timestamp_ms=stamp,
                            conn=conn,
                        )
                    except PaperCommandError as error:
                        conn.execute("ROLLBACK TO auto_command")
                        reason = error.reason_code
                        if reason == "reconciliation_mismatch":
                            account["status"] = "halted"
                            self._failure = reason
                    else:
                        account = self.service._account(self.account_id, conn)
                        reason, order_id = "paper_order_filled", result["order"]["order_id"]
                        current_equity = Decimal(result["account"]["equity_usd"])
                        if (
                            baseline <= 0
                            or (baseline - current_equity) * 100 / baseline
                            >= cfg.max_daily_loss_pct
                        ):
                            state["daily_loss_halted"] = True
                        after = Decimal(account["spot_inventory"][base]["quantity"])
                        if side == "buy" or after == 0:
                            cursor[symbol] = stamp
                    finally:
                        conn.execute("RELEASE auto_command")
                decisions.append(
                    {
                        "symbol": symbol,
                        "action": signal.action,
                        "signal_timestamp_ms": stamp,
                        "signal_reason_code": signal.reason_code,
                        "signal_human_reason": signal.human_reason,
                        "signal_close_usdt": decimal_text(signal.close),
                        "signal_reference_usdt": decimal_text(signal.reference),
                        "risk_score": "passed" if order_id else "blocked",
                        "expected_net_pct": None,
                        "estimated_round_trip_cost_pct": decimal_text(
                            2 * (FEE_RATE_PCT + SLIPPAGE_RATE_PCT)
                            + (books[symbol].asks[0].price / books[symbol].bids[0].price - 1) * 100
                        ),
                        "side": side,
                        "reason_code": reason,
                        "human_reason": human_reason(reason),
                        "order_id": order_id,
                    }
                )
            account["auto_signal_cursor"] = cursor
            store.upsert_account(self.account_id, account, conn=conn)
            self.service._positions(self.account_id, account, conn)
            final = self.service.account(self.account_id, conn=conn)
            final_equity = Decimal(final["equity_usd"])
            drawdown = (
                max(Decimal(0), (baseline - final_equity) * 100 / baseline)
                if baseline > 0
                else Decimal(100)
            )
            state.update(
                status="running",
                reason_code="auto_paper_running",
                decisions=decisions,
                equity_usdt=final["equity_usd"],
                daily_drawdown_pct=decimal_text(Decimal(str(drawdown))),
                signal_timestamp_ms=signals[0].candle_timestamp_ms,
                valuation_checked_at_ms=now,
                strategy=VERSION,
            )
            if state["daily_loss_halted"]:
                state.update(status="blocked", reason_code="daily_loss_limit_reached")
            if self._failure:
                state.update(status="blocked", reason_code=self._failure)
            self._save(account, state, conn)
            return dict(_plain(state))

    def _paused_state(self, conn: Any) -> dict[str, Any]:
        account = self.service._account(self.account_id, conn)
        state = {
            **account.get("auto_paper", {}),
            "status": "paused",
            "reason_code": "auto_paper_paused",
            "checked_at_ms": self.clock(),
            "decisions": [],
        }
        self._save(account, state, conn)
        return state

    def control(self, action: str, *, idempotency_key: str) -> dict[str, Any]:
        """Persist pause/resume; the command and audit have durable idempotency."""
        if action not in ("pause", "resume"):
            raise PaperCommandError("invalid_order", "Допустимы pause и resume")
        store = self.service.store
        with store.transaction() as conn:
            account = self.service._account(self.account_id, conn)
            payload = {
                "operation": "auto_paper_control",
                "account_id": self.account_id,
                "action": action,
            }
            replay = self.service._replay(self.account_id, idempotency_key, payload, conn)
            if replay is not None:
                return replay
            if action == "resume":
                result = reconcile_records(
                    account,
                    store.list_balances(self.account_id, conn=conn),
                    store.list_orders(self.account_id, conn=conn),
                    store.list_fills(self.account_id, conn=conn),
                    store.list_positions(self.account_id, conn=conn),
                )
                if account.get("status") != "active" or self._failure or result["status"] != "ok":
                    raise PaperCommandError("reconciliation_mismatch", "Снятие паузы заблокировано")
            account["auto_operator_paused"] = action == "pause"
            store.upsert_account(self.account_id, account, conn=conn)
            reason = "auto_paper_paused" if action == "pause" else "auto_paper_resumed"
            state = {
                **account.get("auto_paper", {}),
                "status": "paused" if action == "pause" else "waiting",
                "reason_code": reason,
                "checked_at_ms": self.clock(),
                "decisions": [],
            }
            self._save(account, state, conn)
            response = {
                "action": action,
                "paused": action == "pause",
                "reason_code": reason,
                "human_reason": human_reason(reason),
                "paper_only": True,
                "live_execution": False,
                "account_id": self.account_id,
            }
            store.insert_audit(
                {
                    "account_id": self.account_id,
                    "actor": "local_paper_user",
                    "event_type": "auto_paper_control",
                    "reason_code": reason,
                    "human_reason": human_reason(reason),
                    "details": response,
                },
                conn=conn,
            )
            store.complete_idempotency(self.account_id, idempotency_key, response, conn=conn)
            return response

    def snapshot(self) -> dict[str, Any]:
        with self.service.store.transaction() as conn:
            account = self.service._account(self.account_id, conn)
            state = dict(account.get("auto_paper", {}))
            state.setdefault("status", "waiting")
            state.setdefault("reason_code", "completed_candles_unavailable")
            if not self.settings.okx_spot_auto_enabled:
                state.update(status="disabled", reason_code="auto_paper_disabled")
            elif account.get("auto_operator_paused"):
                state.update(status="paused", reason_code="auto_paper_paused")
            if self._failure:
                state.update(status="blocked", reason_code=self._failure)
            state["human_reason"] = human_reason(state["reason_code"])
            heartbeat = state.get("checked_at_ms")
            age = None if heartbeat is None else self.clock() - int(heartbeat)
            running = self._task is not None and not self._task.done()
            valuation = state.get("valuation_checked_at_ms")
            return {
                **state,
                "account_id": self.account_id,
                "paper_only": True,
                "live_execution": False,
                "enabled": self.settings.okx_spot_auto_enabled,
                "worker_running": running,
                "heartbeat_age_ms": age,
                "valuation_age_ms": None if valuation is None else self.clock() - int(valuation),
                "orders_count": len(self.service.store.list_orders(self.account_id, conn=conn)),
                "fills_count": len(self.service.store.list_fills(self.account_id, conn=conn)),
                "configuration": _plain(
                    {
                        "strategy": VERSION,
                        "initial_usdt": Decimal(account["initial_balances"]["USDT"]),
                        "max_order_usdt": self.settings.max_trade_notional_usd,
                        "max_asset_pct": self.settings.okx_spot_max_asset_pct,
                        "max_total_pct": self.settings.okx_spot_max_total_pct,
                        "max_daily_loss_pct": self.settings.max_daily_loss_pct,
                        "max_entry_deviation_pct": (
                            self.settings.okx_spot_auto_max_entry_deviation_pct
                        ),
                        "max_spread_pct": self.settings.okx_spot_auto_max_spread_pct,
                        "fee_pct": FEE_RATE_PCT,
                        "slippage_pct": SLIPPAGE_RATE_PCT,
                    }
                ),
            }

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.cycle)
            except Exception:
                self._failure = "auto_paper_execution_error"
                logger.error("auto_paper_cycle status=blocked reason=auto_paper_execution_error")
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), self.settings.okx_spot_auto_interval_seconds
                )

    async def start(self) -> None:
        if self.settings.okx_spot_auto_enabled and self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="automatic-okx-paper")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
