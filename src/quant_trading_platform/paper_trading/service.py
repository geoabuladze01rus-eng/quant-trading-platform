"""Transactional funded paper spreads; never submits orders to an exchange."""

import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from time import time
from typing import Any, cast
from uuid import uuid4

from quant_trading_platform.config import Settings
from quant_trading_platform.explainability.reasons import (
    canonical_reason_code,
    human_reason,
)
from quant_trading_platform.market_data.models import NormalizedOrderBook
from quant_trading_platform.models import ArbitrageOpportunity, normalize_symbol
from quant_trading_platform.paper_trading import PaperExecutionEngine, PaperExecutionReport
from quant_trading_platform.paper_trading.models import (
    OPEN_STATUSES,
    PaperCommand,
    PaperCommandError,
    canonical_hash,
    decimal_text,
)
from quant_trading_platform.paper_trading.reconciliation import reconcile_records
from quant_trading_platform.persistence.store import SQLitePaperStore


class PersistentPaperService:
    """One SQLite transaction covers intent, reservations, fills, balances and audit.

    A command is a paired spread group. Partial execution matches both legs to the
    smaller available depth; its unfilled quote/base reservations remain until
    cancellation. No background replenishment or real-exchange atomicity is implied.
    """

    def __init__(self, store: SQLitePaperStore, engine: PaperExecutionEngine | None = None) -> None:
        self.store = store
        self.engine = engine or PaperExecutionEngine()

    def _account(self, account_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
        account = self.store.get_account(account_id, conn=conn)
        if account is None:
            raise PaperCommandError("account_not_found", "Бумажный счёт не найден")
        return account

    def _replay(
        self, account_id: str, key: str, payload: dict[str, Any], conn: sqlite3.Connection
    ) -> dict[str, Any] | None:
        if not key or len(key) > 128:
            raise PaperCommandError(
                "invalid_order", "Нужен ключ идемпотентности до 128 символов"
            )
        digest = canonical_hash(payload)
        previous = self.store.get_idempotency(account_id, key, conn=conn)
        if previous is not None:
            if previous["request_hash"] != digest:
                raise PaperCommandError(
                    "duplicate_idempotency_key", "Ключ уже использован для другого запроса"
                )
            if previous["status"] != "completed":
                raise PaperCommandError(
                    "duplicate_idempotency_key", "Запрос с этим ключом ещё выполняется"
                )
            return dict(previous["response"])
        self.store.reserve_idempotency(account_id, key, digest, conn=conn)
        return None

    @staticmethod
    def _command(
        opportunity: ArbitrageOpportunity, amount: Decimal, account_id: str
    ) -> PaperCommand:
        if not amount.is_finite() or amount <= 0:
            raise PaperCommandError(
                "invalid_order", "Размер должен быть положительным конечным числом"
            )
        try:
            symbol = normalize_symbol(opportunity.symbol)
        except ValueError as error:
            raise PaperCommandError("market_mismatch", "Некорректный символ") from error
        return PaperCommand(
            account_id,
            symbol,
            str(opportunity.buy_exchange),
            str(opportunity.sell_exchange),
            amount,
        )

    def _plan(
        self,
        opportunity: ArbitrageOpportunity,
        buy_book: NormalizedOrderBook | None,
        sell_book: NormalizedOrderBook | None,
        amount: Decimal,
        settings: Settings,
    ) -> PaperExecutionReport:
        # Always gate the full requested amount before considering a smaller depth fill.
        report = self.engine.simulate(
            opportunity, buy_book, sell_book, notional_usd=amount, settings=settings
        )
        if report.reason_code != "insufficient_depth" or buy_book is None or sell_book is None:
            return report
        sell_capacity = sum((level.quantity for level in sell_book.bids), Decimal(0))
        remaining_quote, used_quote = amount, Decimal(0)
        for level in sorted(buy_book.asks, key=lambda item: item.price):
            size = min(level.quantity, sell_capacity, remaining_quote / level.price)
            cost = size * level.price
            used_quote += cost
            remaining_quote -= cost
            sell_capacity -= size
            if remaining_quote <= 0 or sell_capacity <= 0:
                break
        if used_quote <= 0:
            return report
        return self.engine.simulate(
            opportunity, buy_book, sell_book, notional_usd=used_quote, settings=settings
        )

    def preview(
        self,
        opportunity: ArbitrageOpportunity,
        buy_book: NormalizedOrderBook | None,
        sell_book: NormalizedOrderBook | None,
        *,
        notional_usd: Decimal,
        settings: Settings,
        account_id: str = "paper-default",
    ) -> dict[str, Any]:
        command = self._command(opportunity, notional_usd, account_id)
        with self.store.transaction() as conn:
            self._account(account_id, conn)
            # A separate in-memory engine keeps previews out of execution history.
            planner = PersistentPaperService(self.store, PaperExecutionEngine())
            report = planner._plan(opportunity, buy_book, sell_book, notional_usd, settings)
            funds = (
                self._funding(command, opportunity, buy_book, conn)
                if report.fills
                else (Decimal(0), Decimal(0), None)
            )
            reason = funds[2]
            if command.symbol not in ("BTC/USDT", "ETH/USDT"):
                reason = "unsupported_market"
            rejected = reason is not None or not report.fills
            partial = bool(report.fills and report.fills[0].notional_usd < notional_usd)
            code = canonical_reason_code(
                reason
                or (
                    report.reason_code
                    if rejected
                    else "paper_order_partially_filled"
                    if partial
                    else "paper_order_filled"
                )
            )
            return {
                "account_id": account_id,
                "preview": True,
                "status": "rejected" if rejected else "partially_filled" if partial else "filled",
                "reason_code": code,
                "human_reason": human_reason(code),
                "reason_text": human_reason(code),
                "requested_notional_usd": decimal_text(notional_usd),
                "simulated_notional_usd": decimal_text(report.fills[0].notional_usd)
                if report.fills
                else "0",
                "required_quote": decimal_text(funds[0]),
                "required_base": decimal_text(funds[1]),
                "reconciliation": _plain(asdict(report.reconciliation)),
            }

    def _funding(
        self,
        command: PaperCommand,
        opportunity: ArbitrageOpportunity,
        buy_book: NormalizedOrderBook | None,
        conn: sqlite3.Connection,
    ) -> tuple[Decimal, Decimal, str | None]:
        if (
            buy_book is None
            or not buy_book.asks
            or not opportunity.fees_pct.is_finite()
            or not opportunity.slippage_pct.is_finite()
        ):
            return Decimal(0), Decimal(0), None
        price = min(level.price for level in buy_book.asks)
        if not price.is_finite() or price <= 0:
            return Decimal(0), Decimal(0), None
        quote = command.notional_usd * (
            1 + opportunity.fees_pct / 200 + opportunity.slippage_pct / 100
        )
        base = command.notional_usd / price
        quote_balance = self.store.get_balance(command.account_id, "USDT", conn=conn)
        base_balance = self.store.get_balance(
            command.account_id, command.symbol.split("/")[0], conn=conn
        )
        if (
            quote_balance is None
            or base_balance is None
            or Decimal(quote_balance["available"]) < quote
            or Decimal(base_balance["available"]) < base
        ):
            return quote, base, "insufficient_paper_balance"
        return quote, base, None

    def execute(
        self,
        opportunity: ArbitrageOpportunity,
        buy_book: NormalizedOrderBook | None,
        sell_book: NormalizedOrderBook | None,
        *,
        notional_usd: Decimal,
        settings: Settings,
        idempotency_key: str,
        account_id: str = "paper-default",
        actor: str = "local",
    ) -> dict[str, Any]:
        command = self._command(opportunity, notional_usd, account_id)
        with self.store.transaction() as conn:
            account = self._account(account_id, conn)
            replay = self._replay(account_id, idempotency_key, command.payload(), conn)
            if replay is not None:
                return replay
            if account.get("status") != "active":
                raise PaperCommandError(
                    "reconciliation_mismatch",
                    "Счёт остановлен: сначала устраните расхождение по сверке.",
                )
            order_id = str(uuid4())
            order: dict[str, Any] = {
                "order_id": order_id,
                "execution_id": order_id,
                "account_id": account_id,
                "symbol": command.symbol,
                "side": "spread",
                "status": "created",
                "buy_venue": command.buy_venue,
                "sell_venue": command.sell_venue,
                "requested_notional_usd": notional_usd,
                "remaining_notional_usd": notional_usd,
                "filled_quantity": Decimal(0),
                "reserved_quote": Decimal(0),
                "reserved_base": Decimal(0),
                "timestamp_ms": int(time() * 1000),
                "strategy": opportunity.strategy,
                "market_type": opportunity.market_type.value,
                "gross_edge": opportunity.expected_gross_pct,
                "fees": opportunity.fees_pct,
                "fee_rate_pct": opportunity.fees_pct / 2,
                "slippage": opportunity.slippage_pct,
                "net_edge": opportunity.expected_net_pct,
                "algorithm_version": settings.paper_algorithm_version,
                "legs": [
                    {"side": "buy", "venue": command.buy_venue},
                    {"side": "sell", "venue": command.sell_venue},
                ],
            }
            self.store.insert_order(order, conn=conn)
            self._audit(order, actor, "paper_order_created", conn)
            report = self._plan(opportunity, buy_book, sell_book, notional_usd, settings)
            order["data_age_ms"] = report.reconciliation.data_age_ms
            quote_reserve, base_reserve, funding_error = (
                self._funding(command, opportunity, buy_book, conn)
                if report.fills
                else (Decimal(0), Decimal(0), None)
            )
            rejection = funding_error if report.fills else canonical_reason_code(report.reason_code)
            if command.symbol not in ("BTC/USDT", "ETH/USDT"):
                rejection = "unsupported_market"
            if rejection:
                order.update(
                    status="rejected",
                    reason_code=rejection,
                    reason_text=human_reason(rejection),
                    human_reason=human_reason(rejection),
                )
                self.store.update_order(order, conn=conn)
                self._audit(order, actor, "paper_order_rejected", conn)
                response = _plain(
                    {
                        "order": order,
                        "order_id": order_id,
                        "status": "rejected",
                        "reason_code": rejection,
                        "reason_text": order["reason_text"],
                        "human_reason": order["human_reason"],
                        "fills": [],
                        "account": self.account(account_id, conn=conn),
                    }
                )
            else:
                base = command.symbol.split("/")[0]
                self._change_balance(account_id, "USDT", -quote_reserve, quote_reserve, conn)
                self._change_balance(account_id, base, -base_reserve, base_reserve, conn)
                order.update(
                    status="accepted", reserved_quote=quote_reserve, reserved_base=base_reserve
                )
                self.store.update_order(order, conn=conn)
                self._audit(order, actor, "paper_order_accepted", conn)
                bought, sold = report.fills
                reserve_cost = report.reconciliation.slippage_cost_usd
                spent = bought.notional_usd + bought.fee_usd + reserve_cost
                self._change_balance(
                    account_id, "USDT", sold.notional_usd - sold.fee_usd, -spent, conn
                )
                self._change_balance(account_id, base, bought.quantity, -sold.quantity, conn)
                remaining = max(Decimal(0), notional_usd - bought.notional_usd)
                quote_left = max(Decimal(0), quote_reserve - spent)
                base_left = max(Decimal(0), base_reserve - sold.quantity)
                if remaining == 0:
                    self._change_balance(account_id, "USDT", quote_left, -quote_left, conn)
                    self._change_balance(account_id, base, base_left, -base_left, conn)
                    quote_left = base_left = Decimal(0)
                order.update(
                    status="partially_filled" if remaining else "filled",
                    remaining_notional_usd=remaining,
                    filled_quantity=bought.quantity,
                    reserved_quote=quote_left,
                    reserved_base=base_left,
                    reason_code=(
                        "paper_order_partially_filled" if remaining else "paper_order_filled"
                    ),
                    reconciliation=asdict(report.reconciliation),
                )
                order["human_reason"] = human_reason(order["reason_code"])
                order["reason_text"] = order["human_reason"]
                fills = []
                for fill in report.fills:
                    record = asdict(fill)
                    record.update(
                        account_id=account_id,
                        execution_id=order_id,
                        order_id=order_id,
                        leg_order_id=fill.order_id,
                        fee_rate_pct=opportunity.fees_pct / 2,
                        slippage_cost_usd=reserve_cost if fill.side == "buy" else Decimal(0),
                    )
                    fills.append(self.store.insert_fill(record, conn=conn))
                self.store.update_order(order, conn=conn)
                pnl = (
                    sold.notional_usd
                    - bought.notional_usd
                    - bought.fee_usd
                    - sold.fee_usd
                    - reserve_cost
                )
                account.update(
                    realized_pnl_usd=Decimal(account.get("realized_pnl_usd", "0")) + pnl,
                    fees_paid_usd=Decimal(account.get("fees_paid_usd", "0"))
                    + bought.fee_usd
                    + sold.fee_usd,
                    slippage_paid_usd=Decimal(account.get("slippage_paid_usd", "0")) + reserve_cost,
                )
                # A validated paper fill may provide a mark, but never invents a
                # cost basis for pre-funded inventory. Unknown P&L stays explicit.
                marks = dict(account.get("marks", {}))
                mark_sources = dict(account.get("mark_sources", {}))
                marks[base] = decimal_text(bought.price)
                mark_sources[base] = "last_validated_paper_buy_fill"
                account["marks"] = marks
                account["mark_sources"] = mark_sources
                self.store.upsert_account(account_id, account, conn=conn)
                self._positions(account_id, account, conn)
                self._audit(order, actor, "paper_order_" + order["status"], conn)
                response = _plain(
                    {
                        "order": order,
                        "order_id": order_id,
                        "status": order["status"],
                        "reason_code": order["reason_code"],
                        "reason_text": order["reason_text"],
                        "human_reason": order["human_reason"],
                        "fills": fills,
                        "account": self.account(account_id, conn=conn),
                    }
                )
            response.update(self._snapshot(account_id, conn))
            self.store.complete_idempotency(account_id, idempotency_key, response, conn=conn)
            return dict(response)

    def _change_balance(
        self,
        account_id: str,
        asset: str,
        available: Decimal,
        reserved: Decimal,
        conn: sqlite3.Connection,
    ) -> None:
        balance = self.store.get_balance(account_id, asset, conn=conn)
        if balance is None:
            raise PaperCommandError("balance_missing", "Баланс актива не найден")
        self.store.upsert_balance(
            account_id,
            asset,
            Decimal(balance["available"]) + available,
            Decimal(balance["reserved"]) + reserved,
            conn=conn,
        )

    def _audit(
        self, order: dict[str, Any], actor: str, action: str, conn: sqlite3.Connection
    ) -> None:
        self.store.insert_audit(
            {
                "account_id": order["account_id"],
                "execution_id": order["execution_id"],
                "correlation_id": order["order_id"],
                "event_id": str(uuid4()),
                "timestamp": datetime.now(UTC).isoformat(),
                "actor": actor,
                "actor_type": "user" if actor != "system" else "system",
                "event_type": action,
                "strategy": order.get("strategy", "cross_venue_spread"),
                "symbol": order["symbol"],
                "venue": f"{order.get('buy_venue', '')}->{order.get('sell_venue', '')}",
                "market_type": order.get("market_type", "crypto"),
                "order_id": order["order_id"],
                "opportunity_id": order.get("opportunity_id", ""),
                "decision": order["status"],
                "reason_code": order.get("reason_code", action),
                "human_reason": human_reason(order.get("reason_code", action)),
                "risk_score": "blocked" if order["status"] in ("rejected", "failed") else "safe",
                "gross_edge": order.get("gross_edge"),
                "fees": order.get("fees"),
                "slippage": order.get("slippage"),
                "net_edge": order.get("net_edge"),
                "data_age_ms": order.get(
                    "data_age_ms", order.get("reconciliation", {}).get("data_age_ms")
                ),
                "algorithm_version": order.get("algorithm_version", "paper-alpha-v1"),
            },
            conn=conn,
        )

    def cancel(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        account_id: str = "paper-default",
        actor: str = "local",
    ) -> dict[str, Any]:
        with self.store.transaction() as conn:
            self._account(account_id, conn)
            replay = self._replay(
                account_id,
                idempotency_key,
                {"operation": "cancel", "account_id": account_id, "order_id": order_id},
                conn,
            )
            if replay is not None:
                return replay
            order = self.store.get_order(order_id, conn=conn)
            if order is None or order["account_id"] != account_id:
                raise PaperCommandError("order_not_found", "Бумажный ордер не найден")
            if order["status"] not in OPEN_STATUSES:
                raise PaperCommandError("order_not_cancellable", "Ордер уже закрыт")
            quote, base = Decimal(order["reserved_quote"]), Decimal(order["reserved_base"])
            self._change_balance(account_id, "USDT", quote, -quote, conn)
            self._change_balance(account_id, order["symbol"].split("/")[0], base, -base, conn)
            order.update(
                status="cancelled",
                reserved_quote=Decimal(0),
                reserved_base=Decimal(0),
                reason_code="paper_order_cancelled",
                reason_text=human_reason("paper_order_cancelled"),
                human_reason=human_reason("paper_order_cancelled"),
            )
            self.store.update_order(order, conn=conn)
            self._audit(order, actor, "paper_order_cancelled", conn)
            response = _plain(
                {
                    "order": order,
                    "order_id": order_id,
                    "status": "cancelled",
                    "reason_code": order["reason_code"],
                    "reason_text": order["reason_text"],
                    "human_reason": order["human_reason"],
                    "account": self.account(account_id, conn=conn),
                }
            )
            response.update(self._snapshot(account_id, conn))
            self.store.complete_idempotency(account_id, idempotency_key, response, conn=conn)
            return dict(response)

    def _snapshot(self, account_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
        account = self.account(account_id, conn=conn)
        positions = self.store.list_positions(account_id, conn=conn)
        reconciliation = reconcile_records(
            self._account(account_id, conn),
            self.store.list_balances(account_id, conn=conn),
            self.store.list_orders(account_id, conn=conn),
            self.store.list_fills(account_id, conn=conn),
            positions,
        )
        return cast(
            dict[str, Any],
            _plain(
                {
                    "account": account,
                    "balances": account["balances"],
                    "positions": positions,
                    "accounting_reconciliation": reconciliation,
                }
            ),
        )

    def _positions(
        self, account_id: str, account: dict[str, Any], conn: sqlite3.Connection
    ) -> None:
        for balance in self.store.list_balances(account_id, conn=conn):
            asset = balance["asset"]
            if asset == "USDT":
                continue
            self.store.upsert_position(
                {
                    "id": f"{account_id}:{asset}",
                    "account_id": account_id,
                    "asset": asset,
                    "symbol": f"{asset}/USDT",
                    "quantity": Decimal(balance["available"]) + Decimal(balance["reserved"]),
                    "cost_basis": account.get("cost_basis", {}).get(asset),
                    "mark_price": account.get("marks", {}).get(asset),
                },
                conn=conn,
            )

    def account(
        self, account_id: str = "paper-default", *, conn: sqlite3.Connection | None = None
    ) -> dict[str, Any]:
        if conn is None:
            with self.store.transaction() as db:
                return self.account(account_id, conn=db)
        account = self._account(account_id, conn)
        balances = self.store.list_balances(account_id, conn=conn)
        known_equity, unrealized = Decimal(0), Decimal(0)
        unpriced: list[str] = []
        unknown_cost_basis: list[str] = []
        for balance in balances:
            total = Decimal(balance["available"]) + Decimal(balance["reserved"])
            balance["total"] = decimal_text(total)
            asset = balance["asset"]
            mark = "1" if asset == "USDT" else account.get("marks", {}).get(asset)
            if mark is None:
                if total:
                    unpriced.append(asset)
                continue
            known_equity += total * Decimal(mark)
            basis = account.get("cost_basis", {}).get(asset)
            if basis is None:
                if total and asset != "USDT":
                    unknown_cost_basis.append(asset)
            else:
                unrealized += total * (Decimal(mark) - Decimal(basis))
        return cast(
            dict[str, Any],
            _plain(
                {
                    **account,
                    "account_id": account_id,
                    "balances": balances,
                    "equity_usd": None if unpriced else known_equity,
                    "virtual_equity_usdt": None if unpriced else known_equity,
                    "priced_equity_usd": known_equity,
                    "unpriced_assets": unpriced,
                    "unpriced_pnl_assets": unknown_cost_basis,
                    "mark_sources": account.get("mark_sources", {}),
                    "unrealized_pnl_usd": None if unpriced or unknown_cost_basis else unrealized,
                    "unrealized_pnl_usdt": None if unpriced or unknown_cost_basis else unrealized,
                    "realized_pnl_usd": account.get("realized_pnl_usd", "0"),
                    "realized_pnl_usdt": account.get("realized_pnl_usd", "0"),
                    "fees_paid_usd": account.get("fees_paid_usd", "0"),
                    "fees_paid_usdt": account.get("fees_paid_usd", "0"),
                    "slippage_paid_usd": account.get("slippage_paid_usd", "0"),
                    "slippage_cost_usdt": account.get("slippage_paid_usd", "0"),
                }
            ),
        )

    def recover(self, account_id: str = "paper-default") -> dict[str, Any]:
        """Persist a startup reconciliation snapshot and halt unsafe paper commands."""
        with self.store.transaction() as conn:
            account = self._account(account_id, conn)
            result = reconcile_records(
                account,
                self.store.list_balances(account_id, conn=conn),
                self.store.list_orders(account_id, conn=conn),
                self.store.list_fills(account_id, conn=conn),
                self.store.list_positions(account_id, conn=conn),
            )
            recovered = result["status"] == "ok"
            account.update(
                status="active" if recovered else "halted",
                recovery_reason="approved" if recovered else "reconciliation_mismatch",
                recovery_checked_at=result["checked_at"],
            )
            self.store.upsert_account(account_id, account, conn=conn)
            snapshot = self.store.insert_reconciliation(
                {
                    **result,
                    "account_id": account_id,
                    "status": result["status"],
                    "execution_id": "",
                    "correlation_id": account_id,
                },
                conn=conn,
            )
            return {**result, "account_status": account["status"], "snapshot_id": snapshot["id"]}

    def reconcile(
        self, account_id: str = "paper-default", *, persist: bool = False
    ) -> dict[str, Any]:
        with self.store.transaction() as conn:
            result = reconcile_records(
                self._account(account_id, conn),
                self.store.list_balances(account_id, conn=conn),
                self.store.list_orders(account_id, conn=conn),
                self.store.list_fills(account_id, conn=conn),
                self.store.list_positions(account_id, conn=conn),
            )
            if persist:
                return self.store.insert_reconciliation(result, conn=conn)
            return result


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
