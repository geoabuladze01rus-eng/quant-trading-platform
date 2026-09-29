"""Transactional funded paper spreads; never submits orders to an exchange."""

import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from time import time
from typing import Any, cast
from uuid import uuid4

from quant_trading_platform.config import Settings, TradingMode
from quant_trading_platform.crypto_universe import crypto_spot_pair
from quant_trading_platform.explainability.reasons import (
    canonical_reason_code,
    human_reason,
)
from quant_trading_platform.market_data.models import (
    NormalizedOrderBook,
    StaleMarketDataError,
    normalize_order_book,
)
from quant_trading_platform.models import ArbitrageOpportunity, MarketType, Venue, normalize_symbol
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
            try:
                crypto_spot_pair(command.symbol)
            except ValueError:
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
        try:
            pair = crypto_spot_pair(command.symbol)
        except ValueError:
            return Decimal(0), Decimal(0), "unsupported_market"
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
        quote_balance = self.store.get_balance(command.account_id, pair.quote_asset, conn=conn)
        base_balance = self.store.get_balance(command.account_id, pair.base_asset, conn=conn)
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
            try:
                crypto_spot_pair(command.symbol)
            except ValueError:
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
                pair = crypto_spot_pair(command.symbol)
                self._change_balance(
                    account_id, pair.quote_asset, -quote_reserve, quote_reserve, conn
                )
                self._change_balance(
                    account_id, pair.base_asset, -base_reserve, base_reserve, conn
                )
                order.update(
                    status="accepted", reserved_quote=quote_reserve, reserved_base=base_reserve
                )
                self.store.update_order(order, conn=conn)
                self._audit(order, actor, "paper_order_accepted", conn)
                bought, sold = report.fills
                reserve_cost = report.reconciliation.slippage_cost_usd
                spent = bought.notional_usd + bought.fee_usd + reserve_cost
                self._change_balance(
                    account_id,
                    pair.quote_asset,
                    sold.notional_usd - sold.fee_usd,
                    -spent,
                    conn,
                )
                self._change_balance(
                    account_id, pair.base_asset, bought.quantity, -sold.quantity, conn
                )
                remaining = max(Decimal(0), notional_usd - bought.notional_usd)
                quote_left = max(Decimal(0), quote_reserve - spent)
                base_left = max(Decimal(0), base_reserve - sold.quantity)
                if remaining == 0:
                    self._change_balance(
                        account_id, pair.quote_asset, quote_left, -quote_left, conn
                    )
                    self._change_balance(
                        account_id, pair.base_asset, base_left, -base_left, conn
                    )
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
                marks[pair.base_asset] = decimal_text(bought.price)
                mark_sources[pair.base_asset] = "last_validated_paper_buy_fill"
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

    def execute_spot(
        self,
        *,
        symbol: str,
        venue: Venue,
        side: str,
        book: NormalizedOrderBook | None,
        quantity: Decimal,
        fee_rate_pct: Decimal,
        slippage_pct: Decimal,
        expected_net_edge_pct: Decimal,
        settings: Settings,
        idempotency_key: str,
        strategy: str = "momentum_mean_reversion",
        account_id: str = "paper-default",
        actor: str = "crypto_paper_robot",
    ) -> dict[str, Any]:
        """Fill one local spot side from public depth; never call a venue order API."""
        try:
            normalized = normalize_symbol(symbol)
            pair = crypto_spot_pair(normalized)
        except ValueError as error:
            raise PaperCommandError("unsupported_market", "Unsupported paper symbol") from error
        values = (quantity, fee_rate_pct, slippage_pct, expected_net_edge_pct)
        if (
            side not in ("buy", "sell")
            or not quantity.is_finite()
            or quantity <= 0
            or any(not value.is_finite() for value in values)
            or fee_rate_pct < 0
            or slippage_pct < 0
        ):
            raise PaperCommandError("invalid_order", "Invalid directional paper order")
        payload = {
            "operation": "execute_spot",
            "account_id": account_id,
            "symbol": normalized,
            "venue": venue.value,
            "side": side,
            "quantity": decimal_text(quantity),
            "strategy": strategy,
        }
        with self.store.transaction() as conn:
            account = self._account(account_id, conn)
            replay = self._replay(account_id, idempotency_key, payload, conn)
            if replay is not None:
                return replay
            if account.get("status") != "active":
                raise PaperCommandError(
                    "reconciliation_mismatch", "Paper account is halted by reconciliation"
                )
            order_id = str(uuid4())
            timestamp_ms = int(time() * 1000)
            order: dict[str, Any] = {
                "order_id": order_id,
                "execution_id": order_id,
                "account_id": account_id,
                "symbol": normalized,
                "side": side,
                "status": "created",
                "venue": venue.value,
                "buy_venue": venue.value if side == "buy" else "",
                "sell_venue": venue.value if side == "sell" else "",
                "requested_quantity": quantity,
                "requested_notional_usd": Decimal(0),
                "remaining_notional_usd": Decimal(0),
                "filled_quantity": Decimal(0),
                "reserved_quote": Decimal(0),
                "reserved_base": Decimal(0),
                "timestamp_ms": timestamp_ms,
                "strategy": strategy,
                "market_type": "crypto",
                "gross_edge": expected_net_edge_pct + fee_rate_pct + slippage_pct,
                "fees": fee_rate_pct,
                "fee_rate_pct": fee_rate_pct,
                "slippage": slippage_pct,
                "net_edge": expected_net_edge_pct,
                "algorithm_version": settings.paper_algorithm_version,
                "legs": [{"side": side, "venue": venue.value}],
            }
            self.store.insert_order(order, conn=conn)
            self._audit(order, actor, "paper_order_created", conn)

            rejection: str | None = None
            levels: tuple[Any, ...] = ()
            if (
                settings.trading_mode != TradingMode.PAPER
                or settings.live_trading_enabled
                or settings.live_order_acceptance_gate
            ):
                rejection = "live_trading_locked"
            elif expected_net_edge_pct < settings.min_expected_net_pct:
                rejection = "insufficient_net_edge"
            elif book is None:
                rejection = "source_unavailable"
            else:
                try:
                    if (
                        book.venue != venue
                        or book.symbol != normalized
                        or book.market_type != MarketType.CRYPTO
                    ):
                        raise ValueError("Order book identity mismatch")
                    normalize_order_book(
                        book.venue,
                        book.symbol,
                        [(level.price, level.quantity) for level in book.bids],
                        [(level.price, level.quantity) for level in book.asks],
                        book.timestamp_ms,
                        book.received_at_ms,
                        settings.max_market_data_age_ms,
                        book.timestamp_source,
                        book.market_type,
                    )
                    current_age = timestamp_ms - min(
                        book.timestamp_ms, book.received_at_ms
                    )
                    if max(book.timestamp_ms, book.received_at_ms) > timestamp_ms:
                        raise ValueError("Future order book timestamp")
                    if current_age > settings.max_market_data_age_ms:
                        raise StaleMarketDataError("Stale order book")
                    levels = tuple(
                        sorted(
                            book.asks if side == "buy" else book.bids,
                            key=lambda level: level.price,
                            reverse=side == "sell",
                        )
                    )
                    order["data_age_ms"] = current_age
                except StaleMarketDataError:
                    rejection = "stale_market_data"
                except ValueError:
                    rejection = "invalid_order"
            remaining = quantity
            notional = Decimal(0)
            if rejection is None:
                for level in levels:
                    used = min(remaining, level.quantity)
                    notional += used * level.price
                    remaining -= used
                    if remaining == 0:
                        break
                if remaining:
                    rejection = "depth_insufficient"
                elif notional > settings.max_trade_notional_usd:
                    rejection = "risk_limit_exceeded"
            fee = notional * fee_rate_pct / 100
            slippage_cost = notional * slippage_pct / 100
            inventory = dict(account.get("strategy_inventory", {}))
            entry = dict(inventory.get(pair.base_asset, {}))
            owned_quantity = Decimal(str(entry.get("quantity", "0")))
            owned_cost = Decimal(str(entry.get("cost_usdt", "0")))
            quote_balance = self.store.get_balance(account_id, pair.quote_asset, conn=conn)
            base_balance = self.store.get_balance(account_id, pair.base_asset, conn=conn)
            if rejection is None and (quote_balance is None or base_balance is None):
                rejection = "insufficient_paper_balance"
            elif rejection is None and side == "buy" and quote_balance is not None:
                if Decimal(quote_balance["available"]) < notional + fee + slippage_cost:
                    rejection = "insufficient_paper_balance"
            elif rejection is None and side == "sell" and base_balance is not None:
                if owned_quantity < quantity:
                    rejection = "strategy_position_unavailable"
                elif Decimal(base_balance["available"]) < quantity:
                    rejection = "insufficient_paper_balance"
            if rejection is not None:
                code = canonical_reason_code(rejection)
                order.update(
                    status="rejected",
                    reason_code=code,
                    reason_text=human_reason(code),
                    human_reason=human_reason(code),
                )
                self.store.update_order(order, conn=conn)
                self._audit(order, actor, "paper_order_rejected", conn)
                response = _plain(
                    {
                        "order": order,
                        "order_id": order_id,
                        "status": "rejected",
                        "reason_code": code,
                        "reason_text": order["reason_text"],
                        "human_reason": order["human_reason"],
                        "fills": [],
                        "account": self.account(account_id, conn=conn),
                    }
                )
            else:
                average_price = notional / quantity
                realized = Decimal(0)
                if side == "buy":
                    total_cost = notional + fee + slippage_cost
                    self._change_balance(
                        account_id, pair.quote_asset, -total_cost, Decimal(0), conn
                    )
                    self._change_balance(
                        account_id, pair.base_asset, quantity, Decimal(0), conn
                    )
                    entry = {
                        "quantity": owned_quantity + quantity,
                        "cost_usdt": owned_cost + total_cost,
                    }
                else:
                    proceeds = notional - fee - slippage_cost
                    average_cost = owned_cost / owned_quantity
                    released_cost = average_cost * quantity
                    realized = proceeds - released_cost
                    self._change_balance(
                        account_id, pair.base_asset, -quantity, Decimal(0), conn
                    )
                    self._change_balance(
                        account_id, pair.quote_asset, proceeds, Decimal(0), conn
                    )
                    entry = {
                        "quantity": owned_quantity - quantity,
                        "cost_usdt": owned_cost - released_cost,
                    }
                inventory[pair.base_asset] = entry
                marks = dict(account.get("marks", {}))
                mark_sources = dict(account.get("mark_sources", {}))
                marks[pair.base_asset] = decimal_text(average_price)
                mark_sources[pair.base_asset] = "last_validated_directional_paper_fill"
                account.update(
                    strategy_inventory=inventory,
                    marks=marks,
                    mark_sources=mark_sources,
                    realized_pnl_usd=Decimal(str(account.get("realized_pnl_usd", "0")))
                    + realized,
                    strategy_realized_pnl_usd=Decimal(
                        str(account.get("strategy_realized_pnl_usd", "0"))
                    )
                    + realized,
                    fees_paid_usd=Decimal(str(account.get("fees_paid_usd", "0"))) + fee,
                    slippage_paid_usd=Decimal(str(account.get("slippage_paid_usd", "0")))
                    + slippage_cost,
                )
                self.store.upsert_account(account_id, account, conn=conn)
                fill = self.store.insert_fill(
                    {
                        "fill_id": str(uuid4()),
                        "order_id": order_id,
                        "execution_id": order_id,
                        "account_id": account_id,
                        "leg_order_id": order_id,
                        "venue": venue.value,
                        "symbol": normalized,
                        "side": side,
                        "quantity": quantity,
                        "price": average_price,
                        "notional_usd": notional,
                        "fee_usd": fee,
                        "fee_rate_pct": fee_rate_pct,
                        "slippage_cost_usd": slippage_cost,
                        "timestamp_ms": timestamp_ms,
                        "status": "filled",
                        "reason_code": "paper_order_filled",
                        "reason_text": human_reason("paper_order_filled"),
                    },
                    conn=conn,
                )
                order.update(
                    status="filled",
                    requested_notional_usd=notional,
                    filled_quantity=quantity,
                    average_fill_price=average_price,
                    fee_usd=fee,
                    slippage_cost_usd=slippage_cost,
                    realized_pnl_usd=realized,
                    reason_code="paper_order_filled",
                    reason_text=human_reason("paper_order_filled"),
                    human_reason=human_reason("paper_order_filled"),
                )
                self.store.update_order(order, conn=conn)
                self._positions(account_id, account, conn)
                self._audit(order, actor, "paper_order_filled", conn)
                response = _plain(
                    {
                        "order": order,
                        "order_id": order_id,
                        "status": "filled",
                        "reason_code": order["reason_code"],
                        "reason_text": order["reason_text"],
                        "human_reason": order["human_reason"],
                        "fills": [fill],
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
        next_available = Decimal(balance["available"]) + available
        next_reserved = Decimal(balance["reserved"]) + reserved
        if next_available < 0 or next_reserved < 0:
            raise PaperCommandError(
                "insufficient_paper_balance",
                "Баланс учебного счёта не может стать отрицательным",
            )
        self.store.upsert_balance(
            account_id,
            asset,
            next_available,
            next_reserved,
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
            pair = crypto_spot_pair(str(order["symbol"]))
            quote, base = Decimal(order["reserved_quote"]), Decimal(order["reserved_base"])
            self._change_balance(account_id, pair.quote_asset, quote, -quote, conn)
            self._change_balance(account_id, pair.base_asset, base, -base, conn)
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

    def update_marks(
        self,
        marks: dict[str, Decimal],
        *,
        account_id: str = "paper-default",
        source: str = "public_read_only_midpoint",
    ) -> dict[str, Any]:
        """Refresh valuation marks only; this cannot create an order or alter balances."""
        if not source or any(
            not value.is_finite() or value <= 0 for value in marks.values()
        ):
            raise ValueError("Invalid paper valuation mark")
        with self.store.transaction() as conn:
            account = self._account(account_id, conn)
            saved_marks = dict(account.get("marks", {}))
            mark_sources = dict(account.get("mark_sources", {}))
            for asset, value in marks.items():
                if asset not in ("BTC", "ETH", "LTC"):
                    raise ValueError("Unsupported paper valuation asset")
                saved_marks[asset] = decimal_text(value)
                mark_sources[asset] = source
            account.update(marks=saved_marks, mark_sources=mark_sources)
            self.store.upsert_account(account_id, account, conn=conn)
            self._positions(account_id, account, conn)
            return self.account(account_id, conn=conn)

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
        strategy_unrealized = Decimal(0)
        strategy_unpriced: list[str] = []
        for asset, raw in account.get("strategy_inventory", {}).items():
            item = dict(raw)
            strategy_quantity = Decimal(str(item.get("quantity", "0")))
            strategy_cost = Decimal(str(item.get("cost_usdt", "0")))
            strategy_mark = account.get("marks", {}).get(asset)
            if strategy_quantity and strategy_mark is None:
                strategy_unpriced.append(str(asset))
            elif strategy_quantity:
                strategy_unrealized += (
                    strategy_quantity * Decimal(str(strategy_mark)) - strategy_cost
                )
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
                    "strategy_realized_pnl_usdt": account.get(
                        "strategy_realized_pnl_usd", "0"
                    ),
                    "strategy_unrealized_pnl_usdt": (
                        None if strategy_unpriced else strategy_unrealized
                    ),
                    "strategy_inventory": account.get("strategy_inventory", {}),
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
