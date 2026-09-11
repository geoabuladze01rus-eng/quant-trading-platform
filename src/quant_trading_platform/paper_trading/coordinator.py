"""Server-owned admission into the canonical paper execution lifecycle."""

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from time import time
from typing import Any

from quant_trading_platform.config import MarketScope, Settings, TradingMode
from quant_trading_platform.execution_orchestrator import (
    ExecutionGroup,
    ExecutionGroupStatus,
    ExecutionOrchestrator,
    FillResult,
    GroupReconciliation,
)
from quant_trading_platform.explainability.reasons import (
    canonical_reason_code,
    human_reason,
)
from quant_trading_platform.market_data.models import (
    NormalizedOrderBook,
    normalize_order_book,
)
from quant_trading_platform.models import ArbitrageOpportunity, MarketType, Venue, normalize_symbol
from quant_trading_platform.paper_trading.reconciliation import reconcile_records
from quant_trading_platform.risk import RiskDecision, RiskEngine, RiskLimits


class ExecutionAdmissionError(ValueError):
    def __init__(self, decision: RiskDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason)


@dataclass(frozen=True)
class ExecutionAdmissionResult:
    execution_group: ExecutionGroup
    risk_decision: RiskDecision
    reconciliation: GroupReconciliation
    fills: tuple[FillResult, ...]
    replayed: bool


class PaperExecutionCoordinator:
    """Risk-revalidate and run one bounded, restart-safe paper group attempt."""

    def __init__(
        self,
        orchestrator: ExecutionOrchestrator,
        *,
        max_hedge_attempts: int = 3,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if type(max_hedge_attempts) is not int or not 1 <= max_hedge_attempts <= 10:
            raise ValueError("max_hedge_attempts must be between 1 and 10")
        self.orchestrator = orchestrator
        self.max_hedge_attempts = max_hedge_attempts
        self._clock_ms = clock_ms or (lambda: int(time() * 1000))

    def execute(
        self,
        opportunity: ArbitrageOpportunity,
        buy_book: NormalizedOrderBook | None,
        sell_book: NormalizedOrderBook | None,
        *,
        requested_qty: Decimal,
        settings: Settings,
        command_id: str,
        hedge_books: Sequence[NormalizedOrderBook] = (),
    ) -> ExecutionAdmissionResult:
        if not command_id or len(command_id) > 128 or "\x00" in command_id:
            raise ValueError("command_id must contain 1 to 128 safe characters")
        existing = self.orchestrator.group_for_correlation(command_id)
        if existing is not None:
            self._assert_same_intent(existing, opportunity, requested_qty)
            if existing.status in (
                ExecutionGroupStatus.COMPLETED,
                ExecutionGroupStatus.HALTED,
            ):
                return self._result(existing, self._stored_decision(existing), replayed=True)
        decision, admitted = self._admit(
            opportunity,
            buy_book,
            sell_book,
            requested_qty=requested_qty,
            settings=settings,
        )
        if not decision.approved or admitted is None or buy_book is None or sell_book is None:
            self._audit_rejection(command_id, opportunity, decision)
            raise ExecutionAdmissionError(decision)
        replayed = existing is not None
        group = existing
        if group is None:
            try:
                group = self.orchestrator.create_group(
                    admitted.symbol,
                    admitted.buy_exchange,
                    admitted.sell_exchange,
                    requested_qty,
                    risk_decision=decision,
                    strategy=admitted.strategy,
                    correlation_id=command_id,
                )
            except sqlite3.IntegrityError:
                group = self.orchestrator.group_for_correlation(command_id)
                if group is None:
                    raise
                self._assert_same_intent(group, admitted, requested_qty)
                replayed = True
            if not group.reservations_active and not group.fills:
                group = self.orchestrator.reserve_group(
                    group.execution_group_id,
                    buy_expected_price=buy_book.asks[0].price,
                )
        elif not group.reservations_active and not group.fills:
            group = self.orchestrator.reserve_group(
                group.execution_group_id,
                buy_expected_price=buy_book.asks[0].price,
            )
        self.orchestrator.submit_fill(
            group.execution_group_id,
            "buy",
            buy_book,
            buy_book.asks[0].price,
            f"{command_id}:buy:0",
        )
        group = self.orchestrator.group(group.execution_group_id)
        if group.status != ExecutionGroupStatus.COMPLETED:
            self.orchestrator.submit_fill(
                group.execution_group_id,
                "sell",
                sell_book,
                sell_book.bids[0].price,
                f"{command_id}:sell:0",
            )
        group = self.orchestrator.group(group.execution_group_id)
        for index, hedge_book in enumerate(hedge_books[: self.max_hedge_attempts]):
            if group.status != ExecutionGroupStatus.HEDGE_REQUIRED:
                break
            self.orchestrator.protective_hedge(
                group.execution_group_id,
                hedge_book,
                f"{command_id}:hedge:{index}",
            )
            group = self.orchestrator.group(group.execution_group_id)
        return self._result(group, decision, replayed=replayed)

    def _admit(
        self,
        opportunity: ArbitrageOpportunity,
        buy_book: NormalizedOrderBook | None,
        sell_book: NormalizedOrderBook | None,
        *,
        requested_qty: Decimal,
        settings: Settings,
    ) -> tuple[RiskDecision, ArbitrageOpportunity | None]:
        if settings.trading_mode != TradingMode.PAPER or settings.live_trading_enabled:
            return RiskDecision(False, "Live trading is locked for this paper lifecycle"), None
        if settings.market_scope == MarketScope.RUSSIAN_STOCKS:
            return RiskDecision(False, "Market type mismatch for crypto paper lifecycle"), None
        if not requested_qty.is_finite() or requested_qty <= 0:
            return RiskDecision(False, "Trade notional must be positive"), None
        if buy_book is None or sell_book is None:
            return RiskDecision(False, "API error: public market data unavailable"), None
        try:
            self._validate_book(opportunity, buy_book, opportunity.buy_exchange, settings)
            self._validate_book(opportunity, sell_book, opportunity.sell_exchange, settings)
        except ValueError as error:
            return RiskDecision(False, str(error)), None
        buy_price = buy_book.asks[0].price
        sell_price = sell_book.bids[0].price
        notional = requested_qty * buy_price
        gross = (sell_price / buy_price - 1) * 100
        admitted = replace(
            opportunity,
            symbol=normalize_symbol(opportunity.symbol),
            expected_gross_pct=gross,
            gross_spread_pct=gross,
            expected_net_pct=gross - opportunity.fees_pct - opportunity.slippage_pct,
            max_notional_usd=notional,
            detected_at_ms=self._clock_ms(),
            source_timestamp_ms=min(buy_book.timestamp_ms, sell_book.timestamp_ms),
            market_type=MarketType.CRYPTO,
        )
        accounting = self._accounting_reconciliation()
        data_age = self._clock_ms() - min(buy_book.timestamp_ms, sell_book.timestamp_ms)
        decision = RiskEngine(
            RiskLimits(
                max_daily_loss_pct=Decimal(str(settings.max_daily_loss_pct)),
                max_trade_notional_usd=Decimal(str(settings.max_trade_notional_usd)),
                min_expected_net_pct=Decimal(str(settings.min_expected_net_pct)),
            )
        ).evaluate(
            admitted,
            data_age_ms=data_age,
            max_data_age_ms=settings.max_market_data_age_ms,
            balance_mismatch=accounting["status"] != "ok",
        )
        return decision, admitted

    def _validate_book(
        self,
        opportunity: ArbitrageOpportunity,
        book: NormalizedOrderBook,
        venue: Venue,
        settings: Settings,
    ) -> None:
        if (
            book.venue != venue
            or book.market_type != MarketType.CRYPTO
            or normalize_symbol(book.symbol) != normalize_symbol(opportunity.symbol)
        ):
            raise ValueError("Market type mismatch in execution admission")
        now = self._clock_ms()
        if book.timestamp_ms > now or book.received_at_ms > now:
            raise ValueError("Future market data timestamp")
        if now - book.timestamp_ms > settings.max_market_data_age_ms:
            raise ValueError("Market data is stale")
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

    def _accounting_reconciliation(self) -> dict[str, Any]:
        store = self.orchestrator.store
        account_id = self.orchestrator.account_id
        with store.transaction() as conn:
            account = store.get_account(account_id, conn=conn)
            if account is None:
                return {"status": "error"}
            return reconcile_records(
                account,
                store.list_balances(account_id, conn=conn),
                store.list_orders(account_id, conn=conn),
                store.list_fills(account_id, conn=conn),
                store.list_positions(account_id, conn=conn),
            )

    def _assert_same_intent(
        self,
        group: ExecutionGroup,
        opportunity: ArbitrageOpportunity,
        requested_qty: Decimal,
    ) -> None:
        if (
            group.symbol != normalize_symbol(opportunity.symbol)
            or group.buy_venue != opportunity.buy_exchange
            or group.sell_venue != opportunity.sell_exchange
            or group.requested_qty != requested_qty
        ):
            raise ValueError("command_id is already bound to a different execution intent")

    def _stored_decision(self, group: ExecutionGroup) -> RiskDecision:
        order = self.orchestrator.store.get_order(group.execution_group_id)
        assert order is not None
        return RiskDecision(
            True,
            str(order.get("risk_reason", "Approved for paper lifecycle")),
            tuple(str(check) for check in order.get("risk_checks", ())),
            str(order.get("risk_reason_code", "approved")),
        )

    def _result(
        self, group: ExecutionGroup, decision: RiskDecision, *, replayed: bool
    ) -> ExecutionAdmissionResult:
        return ExecutionAdmissionResult(
            group,
            decision,
            self.orchestrator.reconcile_group(group.execution_group_id),
            group.fills,
            replayed,
        )

    def _audit_rejection(
        self,
        command_id: str,
        opportunity: ArbitrageOpportunity,
        decision: RiskDecision,
    ) -> None:
        store = self.orchestrator.store
        account_id = self.orchestrator.account_id
        with store.transaction() as conn:
            if store.list_audit(
                account_id,
                correlation_id=command_id,
                event_type="rejected",
                conn=conn,
            ):
                return
            code = canonical_reason_code(decision.reason_code)
            store.insert_audit(
                {
                    "account_id": account_id,
                    "execution_id": command_id,
                    "correlation_id": command_id,
                    "event_id": f"{command_id}:risk-rejected",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "timestamp_ms": self._clock_ms(),
                    "actor": "system",
                    "actor_type": "system",
                    "event_type": "rejected",
                    "event": "rejected",
                    "decision": "rejected",
                    "reason_code": code,
                    "human_reason": human_reason(code),
                    "market_scope": MarketType.CRYPTO.value,
                    "market_type": MarketType.CRYPTO.value,
                    "strategy": opportunity.strategy,
                    "symbol": opportunity.symbol,
                    "venue": (
                        f"{opportunity.buy_exchange.value}->{opportunity.sell_exchange.value}"
                    ),
                    "order_id": "",
                    "opportunity_id": "",
                    "risk_score": "blocked",
                },
                conn=conn,
            )


__all__ = [
    "ExecutionAdmissionError",
    "ExecutionAdmissionResult",
    "PaperExecutionCoordinator",
]
