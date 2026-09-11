"""Canonical durable lifecycle for local paper execution groups.

This module never calls private venue APIs. Normalized public order books are
consumed by the existing :class:`PaperExecutionEngine`, while the existing
``SQLitePaperStore`` remains the only balance, fill and audit ledger.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from time import time
from typing import Any
from uuid import uuid4

from quant_trading_platform.explainability.reasons import human_reason
from quant_trading_platform.market_data.models import (
    NormalizedOrderBook,
    StaleMarketDataError,
    normalize_order_book,
)
from quant_trading_platform.models import MarketType, Venue, normalize_symbol
from quant_trading_platform.paper_trading import PaperExecutionEngine
from quant_trading_platform.persistence.store import SQLitePaperStore, decimal_text
from quant_trading_platform.risk import RiskDecision


class ExecutionGroupStatus(StrEnum):
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    HEDGE_REQUIRED = "HEDGE_REQUIRED"
    COMPLETED = "COMPLETED"
    HALTED = "HALTED"


class ExecutionHaltedError(RuntimeError):
    """The paper runtime requires an explicit operator recovery/reset."""


@dataclass(frozen=True)
class FillResult:
    execution_id: str
    execution_group_id: str
    venue: Venue
    simulated_order_id: str
    requested_qty: Decimal
    filled_qty: Decimal
    remaining_qty: Decimal
    average_fill_price: Decimal
    fee: Decimal
    slippage: Decimal
    timestamp: str
    status: str
    event_id: str
    side: str
    is_hedge: bool = False

    @property
    def venue_order_id(self) -> None:
        """Paper fills intentionally have no real venue order identifier."""
        return None


@dataclass(frozen=True)
class ExecutionGroup:
    execution_id: str
    execution_group_id: str
    account_id: str
    symbol: str
    buy_venue: Venue
    sell_venue: Venue
    requested_qty: Decimal
    buy_filled_qty: Decimal
    sell_filled_qty: Decimal
    residual_qty: Decimal
    status: ExecutionGroupStatus
    fills: tuple[FillResult, ...]
    hedge_attempted: bool
    reserved_quote: Decimal
    reserved_base: Decimal
    reservations_active: bool


@dataclass(frozen=True)
class GroupReconciliation:
    execution_group_id: str
    buy_filled_qty: Decimal
    sell_filled_qty: Decimal
    residual_qty: Decimal
    status: ExecutionGroupStatus
    ok: bool
    issues: tuple[str, ...]


class ExecutionOrchestrator:
    """Stateful paper fills, reconciliation and hedging on one durable ledger."""

    def __init__(
        self,
        store: SQLitePaperStore,
        *,
        account_id: str = "paper-default",
        engine: PaperExecutionEngine | None = None,
        fee_rate_pct: Decimal = Decimal("0.1"),
        max_market_data_age_ms: int = 1_000,
        clock_ms: Callable[[], int] | None = None,
        live_trading_enabled: bool = False,
        trading_mode: str = "paper",
    ) -> None:
        if live_trading_enabled or trading_mode != "paper":
            raise ValueError("Canonical execution requires paper mode with live trading disabled")
        if not fee_rate_pct.is_finite() or not Decimal(0) <= fee_rate_pct <= Decimal(100):
            raise ValueError("fee_rate_pct must be finite and between 0 and 100")
        if type(max_market_data_age_ms) is not int or max_market_data_age_ms <= 0:
            raise ValueError("max_market_data_age_ms must be positive")
        self.store = store
        self.account_id = account_id
        self.engine = engine or PaperExecutionEngine()
        self.fee_rate_pct = fee_rate_pct
        self.max_market_data_age_ms = max_market_data_age_ms
        self._clock_ms = clock_ms or (lambda: int(time() * 1000))

    @property
    def halted(self) -> bool:
        account = self.store.get_account(self.account_id)
        return account is not None and account.get("execution_runtime_status") == "HALTED"

    def create_group(
        self,
        symbol: str,
        buy_venue: Venue,
        sell_venue: Venue,
        requested_qty: Decimal,
        *,
        risk_decision: RiskDecision,
        strategy: str = "cross_venue_spread",
        correlation_id: str | None = None,
    ) -> ExecutionGroup:
        symbol = normalize_symbol(symbol)
        if "/" not in symbol:
            raise ValueError("Paper execution group requires a crypto spot pair")
        if buy_venue == sell_venue or Venue.T_INVEST in (buy_venue, sell_venue):
            raise ValueError("Paper execution group requires two distinct crypto venues")
        if not requested_qty.is_finite() or requested_qty <= 0:
            raise ValueError("requested_qty must be finite and positive")
        if not risk_decision.approved:
            raise ValueError("Approved RiskDecision is required before group creation")
        group_id = str(uuid4())
        correlation = correlation_id or group_id
        with self.store.transaction() as conn:
            account = self.store.get_account(self.account_id, conn=conn)
            if account is None:
                raise ValueError("Paper account does not exist")
            if account.get("execution_runtime_status") == "HALTED":
                raise ExecutionHaltedError(
                    "Paper execution is HALTED; explicit recovery/reset is required"
                )
            record: dict[str, Any] = {
                "id": group_id,
                "order_id": group_id,
                "execution_id": group_id,
                "execution_group_id": group_id,
                "account_id": self.account_id,
                "correlation_id": correlation,
                "kind": "execution_group",
                "symbol": symbol,
                "buy_venue": buy_venue.value,
                "sell_venue": sell_venue.value,
                "requested_qty": requested_qty,
                "filled_quantity": Decimal(0),
                "buy_filled_qty": Decimal(0),
                "sell_filled_qty": Decimal(0),
                "residual_qty": Decimal(0),
                "remaining_notional_usd": Decimal(0),
                "reserved_quote": Decimal(0),
                "reserved_base": Decimal(0),
                "status": ExecutionGroupStatus.OPEN.value,
                "hedge_attempted": False,
                "strategy": strategy,
                "market_type": MarketType.CRYPTO.value,
                "reason_code": "paper_order_created",
                "human_reason": human_reason("paper_order_created"),
                "risk_reason_code": risk_decision.reason_code,
                "risk_reason": risk_decision.reason,
                "risk_checks": risk_decision.checks,
            }
            self.store.insert_order(record, conn=conn)
            self._audit(record, "signal", "paper_order_created", conn)
            self._audit(record, "approved", "approved", conn)
        return self.group(group_id)

    def group(self, execution_group_id: str) -> ExecutionGroup:
        with self.store.transaction() as conn:
            return self._group(execution_group_id, conn)

    def list_groups(self) -> tuple[ExecutionGroup, ...]:
        with self.store.transaction() as conn:
            identifiers = (
                str(order["execution_group_id"])
                for order in self.store.list_orders(self.account_id, conn=conn)
                if order.get("kind") == "execution_group"
            )
            return tuple(self._group(identifier, conn) for identifier in identifiers)

    def reconcile_group(self, execution_group_id: str) -> GroupReconciliation:
        """Verify persisted group totals against the immutable fill-event ledger."""
        with self.store.transaction() as conn:
            group = self._group(execution_group_id, conn)
            buy = sum(
                (fill.filled_qty for fill in group.fills if fill.side == "buy"), Decimal(0)
            )
            sell = sum(
                (fill.filled_qty for fill in group.fills if fill.side == "sell"), Decimal(0)
            )
            residual = buy - sell
            issues: list[str] = []
            if buy != group.buy_filled_qty or sell != group.sell_filled_qty:
                issues.append("fill totals do not match execution group state")
            if residual != group.residual_qty:
                issues.append("residual quantity does not match fill events")
            if group.status == ExecutionGroupStatus.COMPLETED and residual != 0:
                issues.append("completed execution group has residual exposure")
            if group.status == ExecutionGroupStatus.HEDGE_REQUIRED and residual == 0:
                issues.append("hedge-required execution group has no residual exposure")
            return GroupReconciliation(
                execution_group_id,
                buy,
                sell,
                residual,
                group.status,
                not issues,
                tuple(issues),
            )

    def reserve_group(
        self, execution_group_id: str, *, buy_expected_price: Decimal
    ) -> ExecutionGroup:
        """Atomically reserve maximum quote and base requirements for both legs."""
        if not buy_expected_price.is_finite() or buy_expected_price <= 0:
            raise ValueError("buy_expected_price must be finite and positive")
        with self.store.transaction() as conn:
            group = self._group(execution_group_id, conn)
            if group.status != ExecutionGroupStatus.OPEN or group.fills:
                raise ValueError("Only an unfilled OPEN group can be reserved")
            if group.reservations_active:
                return group
            base, quote = group.symbol.split("/")
            base_row = self.store.get_balance(self.account_id, base, conn=conn)
            quote_row = self.store.get_balance(self.account_id, quote, conn=conn)
            if base_row is None or quote_row is None:
                raise ValueError("Paper balance row is missing")
            quote_required = (
                group.requested_qty
                * buy_expected_price
                * (Decimal(1) + self.fee_rate_pct / 100)
            )
            base_required = group.requested_qty
            quote_available = Decimal(str(quote_row["available"]))
            base_available = Decimal(str(base_row["available"]))
            if quote_available < quote_required:
                raise ValueError("Insufficient paper quote balance for group reservation")
            if base_available < base_required:
                raise ValueError("Insufficient paper base balance for group reservation")
            self.store.upsert_balance(
                self.account_id,
                quote,
                quote_available - quote_required,
                Decimal(str(quote_row["reserved"])) + quote_required,
                conn=conn,
            )
            self.store.upsert_balance(
                self.account_id,
                base,
                base_available - base_required,
                Decimal(str(base_row["reserved"])) + base_required,
                conn=conn,
            )
            order = self.store.get_order(execution_group_id, conn=conn)
            assert order is not None
            order.update(
                reserved_quote=quote_required,
                reserved_base=base_required,
                reservations_active=True,
                buy_expected_price=buy_expected_price,
            )
            self.store.update_order(order, conn=conn)
            self._audit(order, "approved", "group_balance_reserved", conn)
        return self.group(execution_group_id)

    def _group(self, execution_group_id: str, conn: Any) -> ExecutionGroup:
        order = self.store.get_order(execution_group_id, conn=conn)
        if (
            order is None
            or order.get("kind") != "execution_group"
            or order.get("account_id") != self.account_id
        ):
            raise KeyError(execution_group_id)
        fills = tuple(
            sorted(
                (
                    self._fill_from_record(fill)
                    for fill in self.store.list_fills(self.account_id, conn=conn)
                    if fill.get("execution_group_id") == execution_group_id
                ),
                key=lambda fill: (fill.timestamp, fill.event_id),
            )
        )
        return ExecutionGroup(
            str(order["execution_id"]),
            execution_group_id,
            self.account_id,
            str(order["symbol"]),
            Venue(str(order["buy_venue"])),
            Venue(str(order["sell_venue"])),
            Decimal(str(order["requested_qty"])),
            Decimal(str(order.get("buy_filled_qty", "0"))),
            Decimal(str(order.get("sell_filled_qty", "0"))),
            Decimal(str(order.get("residual_qty", "0"))),
            ExecutionGroupStatus(str(order["status"])),
            fills,
            bool(order.get("hedge_attempted", False)),
            Decimal(str(order.get("reserved_quote", "0"))),
            Decimal(str(order.get("reserved_base", "0"))),
            bool(order.get("reservations_active", False)),
        )

    def submit_fill(
        self,
        execution_group_id: str,
        side: str,
        order_book: NormalizedOrderBook,
        expected_price: Decimal,
        event_id: str,
    ) -> FillResult:
        with self.store.transaction() as conn:
            return self._submit_fill(
                execution_group_id,
                side,
                order_book,
                expected_price,
                event_id,
                conn,
                is_hedge=False,
            )

    def _submit_fill(
        self,
        execution_group_id: str,
        side: str,
        order_book: NormalizedOrderBook,
        expected_price: Decimal,
        event_id: str,
        conn: Any,
        *,
        is_hedge: bool,
        quantity_limit: Decimal | None = None,
    ) -> FillResult:
        previous = self.store.get_fill(event_id, conn=conn)
        if previous is not None:
            if (
                previous.get("execution_group_id") != execution_group_id
                or previous.get("side") != side
                or previous.get("venue") != order_book.venue.value
                or bool(previous.get("is_hedge")) != is_hedge
            ):
                raise ValueError("Duplicate fill event ID has a different payload")
            return self._fill_from_record(previous)
        group = self._group(execution_group_id, conn)
        if group.status in (ExecutionGroupStatus.COMPLETED, ExecutionGroupStatus.HALTED):
            raise ExecutionHaltedError(f"Execution group is {group.status.value}")
        if not group.reservations_active:
            raise ValueError("Execution group balances must be reserved before fills")
        if side not in ("buy", "sell"):
            raise ValueError("Paper fill side must be buy or sell")
        self._validate_book(group, side, order_book, is_hedge=is_hedge)
        already_filled = group.buy_filled_qty if side == "buy" else group.sell_filled_qty
        requested = quantity_limit or (group.requested_qty - already_filled)
        if requested <= 0:
            raise ValueError("Execution leg is already fully filled")
        simulated = self.engine.simulate_depth_fill(
            order_book,
            side=side,
            quantity=requested,
            expected_price=expected_price,
            fee_rate_pct=self.fee_rate_pct,
        )
        if simulated.filled_qty == 0 and is_hedge:
            raise ValueError("Protective hedge has insufficient liquidity")
        timestamp = datetime.now(UTC).isoformat()
        execution_id = str(uuid4())
        simulated_order_id = str(uuid4())
        notional = simulated.average_fill_price * simulated.filled_qty
        if simulated.filled_qty:
            self._apply_fill_balance(
                group,
                side,
                simulated.filled_qty,
                notional,
                simulated.fee,
                conn,
            )
        record: dict[str, Any] = {
            "id": event_id,
            "fill_id": event_id,
            "event_id": event_id,
            "execution_id": execution_id,
            "execution_group_id": execution_group_id,
            "order_id": execution_group_id,
            "account_id": self.account_id,
            "correlation_id": execution_group_id,
            "venue": order_book.venue.value,
            "venue_order_id": None,
            "simulated_order_id": simulated_order_id,
            "symbol": group.symbol,
            "side": side,
            "requested_qty": requested,
            "filled_qty": simulated.filled_qty,
            "remaining_qty": simulated.remaining_qty,
            "average_fill_price": simulated.average_fill_price,
            "quantity": simulated.filled_qty,
            "price": simulated.average_fill_price,
            "notional_usd": notional,
            "fee": simulated.fee,
            "fee_usd": simulated.fee,
            "fee_rate_pct": self.fee_rate_pct,
            "slippage": simulated.slippage,
            "slippage_cost_usd": Decimal(0),
            "timestamp": timestamp,
            "timestamp_ms": self._clock_ms(),
            "status": simulated.status,
            "is_hedge": is_hedge,
            "reason_code": (
                "paper_order_filled"
                if simulated.status == "filled"
                else "paper_order_partially_filled"
                if simulated.filled_qty
                else "depth_insufficient"
            ),
        }
        stored = self.store.insert_fill(record, conn=conn)
        self._update_group_state(group, stored, conn)
        return self._fill_from_record(stored)

    def protective_hedge(
        self,
        execution_group_id: str,
        order_book: NormalizedOrderBook,
        event_id: str,
        *,
        expected_price: Decimal | None = None,
    ) -> FillResult:
        attempt_started = False
        try:
            with self.store.transaction() as conn:
                existing = self.store.get_fill(event_id, conn=conn)
                if existing is not None:
                    if (
                        existing.get("execution_group_id") != execution_group_id
                        or not bool(existing.get("is_hedge"))
                    ):
                        raise ValueError("Duplicate hedge event ID has a different payload")
                    return self._fill_from_record(existing)
                group = self._group(execution_group_id, conn)
                if group.status != ExecutionGroupStatus.HEDGE_REQUIRED or group.residual_qty == 0:
                    raise ValueError("Execution group does not require a protective hedge")
                side = "sell" if group.residual_qty > 0 else "buy"
                levels = order_book.bids if side == "sell" else order_book.asks
                benchmark = expected_price or (levels[0].price if levels else Decimal(1))
                attempt_started = True
                return self._submit_fill(
                    execution_group_id,
                    side,
                    order_book,
                    benchmark,
                    event_id,
                    conn,
                    is_hedge=True,
                    quantity_limit=abs(group.residual_qty),
                )
        except ExecutionHaltedError:
            raise
        except Exception as error:
            if not attempt_started:
                raise
            self._halt(execution_group_id, str(error))
            raise ExecutionHaltedError(
                "Protective hedge failed; paper runtime is HALTED"
            ) from error

    def complete_group(self, execution_group_id: str) -> ExecutionGroup:
        with self.store.transaction() as conn:
            group = self._group(execution_group_id, conn)
            if group.residual_qty != 0:
                raise ValueError("Execution group cannot complete with residual exposure")
            order = self.store.get_order(execution_group_id, conn=conn)
            assert order is not None
            order.update(
                status=ExecutionGroupStatus.COMPLETED.value,
                reason_code="paper_order_filled",
                human_reason=human_reason("paper_order_filled"),
            )
            self.store.update_order(order, conn=conn)
            self._release_group_reservations(order, conn)
            self._book_completed_group(order, conn)
            self._audit(order, "approved", "paper_order_filled", conn)
        return self.group(execution_group_id)

    def reset_after_recovery(self, *, actor: str = "operator") -> None:
        """Clear the global circuit breaker without rewriting halted group history."""
        with self.store.transaction() as conn:
            account = self.store.get_account(self.account_id, conn=conn)
            if account is None:
                raise ValueError("Paper account does not exist")
            account["execution_runtime_status"] = "ACTIVE"
            account["execution_recovered_at"] = datetime.now(UTC).isoformat()
            account["execution_recovery_actor"] = actor
            self.store.upsert_account(self.account_id, account, conn=conn)
            self.store.insert_audit(
                {
                    "account_id": self.account_id,
                    "execution_id": "runtime",
                    "correlation_id": "runtime",
                    "event_id": str(uuid4()),
                    "timestamp": datetime.now(UTC).isoformat(),
                    "actor": actor,
                    "actor_type": "user",
                    "event_type": "recovery_reset",
                    "decision": "approved",
                    "reason_code": "recovery_reset",
                    "human_reason": human_reason("recovery_reset"),
                    "market_type": MarketType.CRYPTO.value,
                    "strategy": "execution_safety",
                    "symbol": "",
                    "venue": "paper",
                    "order_id": "",
                    "opportunity_id": "",
                    "risk_score": "operator_confirmed",
                },
                conn=conn,
            )

    def _validate_book(
        self,
        group: ExecutionGroup,
        side: str,
        book: NormalizedOrderBook,
        *,
        is_hedge: bool,
    ) -> None:
        expected_venue = group.buy_venue if side == "buy" else group.sell_venue
        if not is_hedge and book.venue != expected_venue:
            raise ValueError("Order book venue does not match execution leg")
        if book.venue == Venue.T_INVEST or book.market_type != MarketType.CRYPTO:
            raise ValueError("Paper crypto execution cannot mix market types")
        if normalize_symbol(book.symbol) != group.symbol:
            raise ValueError("Order book symbol does not match execution group")
        now = self._clock_ms()
        if book.received_at_ms > now or book.timestamp_ms > now:
            raise ValueError("Future market data timestamp")
        if now - book.timestamp_ms > self.max_market_data_age_ms:
            raise StaleMarketDataError("Stale market data")
        normalize_order_book(
            book.venue,
            book.symbol,
            [(level.price, level.quantity) for level in book.bids],
            [(level.price, level.quantity) for level in book.asks],
            book.timestamp_ms,
            book.received_at_ms,
            self.max_market_data_age_ms,
            book.timestamp_source,
            book.market_type,
        )

    def _apply_fill_balance(
        self,
        group: ExecutionGroup,
        side: str,
        quantity: Decimal,
        notional: Decimal,
        fee: Decimal,
        conn: Any,
    ) -> None:
        base, quote = group.symbol.split("/")
        base_row = self.store.get_balance(self.account_id, base, conn=conn)
        quote_row = self.store.get_balance(self.account_id, quote, conn=conn)
        if base_row is None or quote_row is None:
            raise ValueError("Paper balance row is missing")
        base_available = Decimal(str(base_row["available"]))
        quote_available = Decimal(str(quote_row["available"]))
        quote_reserved = Decimal(str(quote_row["reserved"]))
        base_reserved = Decimal(str(base_row["reserved"]))
        order = self.store.get_order(group.execution_group_id, conn=conn)
        assert order is not None
        if side == "buy":
            debit = notional + fee
            reserved_used = min(group.reserved_quote, debit)
            extra_debit = debit - reserved_used
            if quote_available < extra_debit or quote_reserved < reserved_used:
                raise ValueError("Insufficient paper quote balance")
            quote_available -= extra_debit
            quote_reserved -= reserved_used
            base_available += quantity
            order["reserved_quote"] = group.reserved_quote - reserved_used
        else:
            reserved_used = min(group.reserved_base, quantity)
            extra_debit = quantity - reserved_used
            if base_available < extra_debit or base_reserved < reserved_used:
                raise ValueError("Insufficient paper base balance")
            base_available -= extra_debit
            base_reserved -= reserved_used
            quote_available += notional - fee
            order["reserved_base"] = group.reserved_base - reserved_used
        self.store.upsert_balance(
            self.account_id,
            quote,
            quote_available,
            quote_reserved,
            conn=conn,
        )
        self.store.upsert_balance(
            self.account_id,
            base,
            base_available,
            base_reserved,
            conn=conn,
        )
        self.store.update_order(order, conn=conn)
        position = self.store.get_position(f"{self.account_id}:{base}", conn=conn)
        self.store.upsert_position(
            {
                **(position or {}),
                "id": f"{self.account_id}:{base}",
                "account_id": self.account_id,
                "execution_id": group.execution_group_id,
                "correlation_id": group.execution_group_id,
                "asset": base,
                "symbol": group.symbol,
                "quantity": base_available + base_reserved,
                "status": "open" if base_available + base_reserved else "flat",
            },
            conn=conn,
        )
        account = self.store.get_account(self.account_id, conn=conn)
        assert account is not None
        account["fees_paid_usd"] = Decimal(str(account.get("fees_paid_usd", "0"))) + fee
        marks = dict(account.get("marks", {}))
        marks[base] = decimal_text(notional / quantity)
        account["marks"] = marks
        if side == "buy":
            basis = dict(account.get("cost_basis", {}))
            basis.setdefault(base, decimal_text(notional / quantity))
            account["cost_basis"] = basis
        self.store.upsert_account(self.account_id, account, conn=conn)

    def _update_group_state(
        self, group: ExecutionGroup, fill: dict[str, Any], conn: Any
    ) -> None:
        order = self.store.get_order(group.execution_group_id, conn=conn)
        assert order is not None
        buy = group.buy_filled_qty
        sell = group.sell_filled_qty
        filled = Decimal(str(fill["filled_qty"]))
        if fill["side"] == "buy":
            buy += filled
        else:
            sell += filled
        residual = buy - sell
        hedge_attempted = group.hedge_attempted or bool(fill.get("is_hedge"))
        if residual != 0:
            status = ExecutionGroupStatus.HEDGE_REQUIRED
            reason = "hedge_required"
        elif hedge_attempted or (buy >= group.requested_qty and sell >= group.requested_qty):
            status = ExecutionGroupStatus.COMPLETED
            reason = "paper_order_filled"
        elif buy or sell:
            status = ExecutionGroupStatus.PARTIAL
            reason = "paper_order_partially_filled"
        else:
            status = ExecutionGroupStatus.OPEN
            reason = "depth_insufficient"
        if status == ExecutionGroupStatus.COMPLETED and residual != 0:
            raise ValueError("COMPLETED execution group cannot have residual exposure")
        order.update(
            status=status.value,
            buy_filled_qty=buy,
            sell_filled_qty=sell,
            filled_quantity=min(buy, sell),
            residual_qty=residual,
            hedge_attempted=hedge_attempted,
            reason_code=reason,
            human_reason=human_reason(reason),
        )
        self.store.update_order(order, conn=conn)
        if status == ExecutionGroupStatus.COMPLETED and group.status != status:
            self._release_group_reservations(order, conn)
            self._book_completed_group(order, conn)
        event = (
            "stop"
            if status == ExecutionGroupStatus.HALTED
            else "approved"
            if status == ExecutionGroupStatus.COMPLETED
            else "pause"
            if status == ExecutionGroupStatus.HEDGE_REQUIRED
            else "signal"
        )
        self._audit(order, event, reason, conn)

    def _release_group_reservations(self, order: dict[str, Any], conn: Any) -> None:
        if not order.get("reservations_active"):
            return
        base, quote = str(order["symbol"]).split("/")
        quote_release = Decimal(str(order.get("reserved_quote", "0")))
        base_release = Decimal(str(order.get("reserved_base", "0")))
        for asset, amount in ((quote, quote_release), (base, base_release)):
            balance = self.store.get_balance(self.account_id, asset, conn=conn)
            if balance is None or Decimal(str(balance["reserved"])) < amount:
                raise ValueError("Group reservation does not match paper balance")
            self.store.upsert_balance(
                self.account_id,
                asset,
                Decimal(str(balance["available"])) + amount,
                Decimal(str(balance["reserved"])) - amount,
                conn=conn,
            )
        order.update(
            reserved_quote=Decimal(0),
            reserved_base=Decimal(0),
            reservations_active=False,
        )
        self.store.update_order(order, conn=conn)

    def _book_completed_group(self, order: dict[str, Any], conn: Any) -> None:
        if order.get("pnl_booked"):
            return
        all_fills = [
            item
            for item in self.store.list_fills(self.account_id, conn=conn)
            if item.get("execution_group_id") == order["execution_group_id"]
        ]
        cash_flow = sum(
            (
                (Decimal(str(item["notional_usd"])) - Decimal(str(item["fee_usd"])))
                if item["side"] == "sell"
                else -(Decimal(str(item["notional_usd"])) + Decimal(str(item["fee_usd"])))
                for item in all_fills
            ),
            Decimal(0),
        )
        account = self.store.get_account(self.account_id, conn=conn)
        assert account is not None
        account["realized_pnl_usd"] = (
            Decimal(str(account.get("realized_pnl_usd", "0"))) + cash_flow
        )
        self.store.upsert_account(self.account_id, account, conn=conn)
        order["pnl_booked"] = True
        self.store.update_order(order, conn=conn)

    def _halt(self, execution_group_id: str, reason: str) -> None:
        with self.store.transaction() as conn:
            order = self.store.get_order(execution_group_id, conn=conn)
            if order is None:
                return
            order.update(
                status=ExecutionGroupStatus.HALTED.value,
                reason_code="protective_hedge_failed",
                human_reason=human_reason("protective_hedge_failed"),
                halt_detail=reason[:500],
            )
            self.store.update_order(order, conn=conn)
            account = self.store.get_account(self.account_id, conn=conn)
            if account is not None:
                account["execution_runtime_status"] = "HALTED"
                account["execution_halted_at"] = datetime.now(UTC).isoformat()
                self.store.upsert_account(self.account_id, account, conn=conn)
            self._audit(order, "stop", "protective_hedge_failed", conn)

    def _audit(
        self, order: dict[str, Any], event: str, reason_code: str, conn: Any
    ) -> None:
        self.store.insert_audit(
            {
                "account_id": self.account_id,
                "execution_id": order["execution_id"],
                "execution_group_id": order["execution_group_id"],
                "correlation_id": order["correlation_id"],
                "event_id": str(uuid4()),
                "timestamp": datetime.now(UTC).isoformat(),
                "actor": "system",
                "actor_type": "system",
                "event_type": event,
                "event": event,
                "decision": order["status"],
                "reason_code": reason_code,
                "human_reason": human_reason(reason_code),
                "market_scope": "crypto",
                "market_type": MarketType.CRYPTO.value,
                "strategy": order.get("strategy", "cross_venue_spread"),
                "symbol": order["symbol"],
                "venue": f"{order['buy_venue']}->{order['sell_venue']}",
                "order_id": order["order_id"],
                "opportunity_id": "",
                "risk_score": "blocked" if event in ("pause", "stop") else "safe",
                "residual_qty": order.get("residual_qty", "0"),
            },
            conn=conn,
        )

    @staticmethod
    def _fill_from_record(record: dict[str, Any]) -> FillResult:
        return FillResult(
            str(record["execution_id"]),
            str(record["execution_group_id"]),
            Venue(str(record["venue"])),
            str(record["simulated_order_id"]),
            Decimal(str(record["requested_qty"])),
            Decimal(str(record["filled_qty"])),
            Decimal(str(record["remaining_qty"])),
            Decimal(str(record["average_fill_price"])),
            Decimal(str(record["fee"])),
            Decimal(str(record["slippage"])),
            str(record["timestamp"]),
            str(record["status"]),
            str(record["event_id"]),
            str(record["side"]),
            bool(record.get("is_hedge", False)),
        )


__all__ = [
    "ExecutionGroup",
    "ExecutionGroupStatus",
    "ExecutionHaltedError",
    "ExecutionOrchestrator",
    "FillResult",
    "GroupReconciliation",
]
