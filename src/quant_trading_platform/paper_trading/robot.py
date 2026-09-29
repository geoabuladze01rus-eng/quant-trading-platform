"""Thin strategy coordinator over the canonical persistent paper service."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from hashlib import sha256
from time import time
from typing import Protocol

from quant_trading_platform.audit_log import PersistentAuditLog
from quant_trading_platform.backtesting import WalkForwardReport, walk_forward
from quant_trading_platform.config import Settings, TradingMode
from quant_trading_platform.crypto_universe import (
    SUPPORTED_CRYPTO_SPOT_SYMBOLS,
    SpotInstrumentRules,
    size_paper_spread,
)
from quant_trading_platform.explainability.reasons import human_reason
from quant_trading_platform.market_data.models import NormalizedOrderBook
from quant_trading_platform.models import ArbitrageOpportunity, Venue
from quant_trading_platform.paper_trading.models import OPEN_STATUSES
from quant_trading_platform.paper_trading.service import PersistentPaperService
from quant_trading_platform.risk import RiskDecision, RiskEngine, RiskLimits
from quant_trading_platform.strategies.arbitrage import CrossVenueSpreadMonitor
from quant_trading_platform.strategies.directional import (
    DirectionalSignal,
    SignalSide,
    directional_signal,
)


class PaperMarketData(Protocol):
    def book_for_simulation(self, venue: Venue, symbol: str) -> NormalizedOrderBook | None: ...

    def rules_for_simulation(self, venue: Venue, symbol: str) -> SpotInstrumentRules | None: ...


@dataclass(frozen=True)
class RobotSignal:
    symbol: str
    buy_venue: Venue
    sell_venue: Venue
    gross_edge_pct: Decimal
    fees_pct: Decimal
    slippage_pct: Decimal
    net_edge_pct: Decimal
    source_timestamp_ms: int
    data_age_ms: int
    risk_approved: bool
    reason_code: str
    expected_notional_usdt: Decimal

    def public(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "base_asset": self.symbol.split("/")[0],
            "quote_asset": self.symbol.split("/")[1],
            "market_type": "spot",
            "buy_venue": self.buy_venue.value,
            "sell_venue": self.sell_venue.value,
            "gross_edge_pct": str(self.gross_edge_pct),
            "fees_pct": str(self.fees_pct),
            "slippage_pct": str(self.slippage_pct),
            "net_edge_pct": str(self.net_edge_pct),
            "source_timestamp_ms": self.source_timestamp_ms,
            "data_age_ms": self.data_age_ms,
            "risk_score": "passed" if self.risk_approved else "blocked",
            "risk_approved": self.risk_approved,
            "reason_code": self.reason_code,
            "expected_notional_usdt": str(self.expected_notional_usdt),
        }


@dataclass(frozen=True)
class DirectionalRobotSignal:
    symbol: str
    venue: Venue
    side: SignalSide
    reference_price: Decimal
    momentum_pct: Decimal
    deviation_pct: Decimal
    expected_net_edge_pct: Decimal
    source_timestamp_ms: int
    data_age_ms: int
    reason_code: str
    validation: WalkForwardReport

    def public(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "base_asset": self.symbol.split("/")[0],
            "quote_asset": self.symbol.split("/")[1],
            "market_type": "spot",
            "venue": self.venue.value,
            "side": self.side.value,
            "reference_price": str(self.reference_price),
            "momentum_pct": str(self.momentum_pct),
            "deviation_pct": str(self.deviation_pct),
            "net_edge_pct": str(self.expected_net_edge_pct),
            "source_timestamp_ms": self.source_timestamp_ms,
            "data_age_ms": self.data_age_ms,
            "reason_code": self.reason_code,
            "walk_forward": {
                "samples": self.validation.samples,
                "closed_trades": self.validation.closed_trades,
                "winning_trades": self.validation.winning_trades,
                "win_rate_pct": str(self.validation.win_rate_pct),
                "strategy_return_pct": str(self.validation.strategy_return_pct),
                "buy_hold_return_pct": str(self.validation.buy_hold_return_pct),
                "max_drawdown_pct": str(self.validation.max_drawdown_pct),
                "average_trade_net_pct": str(self.validation.average_trade_net_pct),
            },
        }


class CryptoPaperRobot:
    """Bybit-anchored spread strategy; execution remains local and paper-only."""

    def __init__(
        self,
        settings: Settings,
        service: PersistentPaperService,
        audit: PersistentAuditLog | None = None,
    ) -> None:
        self.settings = settings
        self.service = service
        self.audit = audit
        self.state = "disabled" if not settings.crypto_paper_robot_enabled else "scanning"
        self.last_reason_code = (
            "paper_robot_disabled"
            if not settings.crypto_paper_robot_enabled
            else "paper_robot_no_signal"
        )
        self.last_checked_at: str | None = None
        self.last_signal: RobotSignal | DirectionalRobotSignal | None = None
        self.last_result: dict[str, object] | None = None
        self.symbol_states: dict[str, dict[str, object]] = {
            symbol: self._symbol_state(symbol, "disabled", "paper_robot_disabled")
            for symbol in SUPPORTED_CRYPTO_SPOT_SYMBOLS
        }
        self._last_decisions: dict[str, str] = {}
        self._last_attempt_ms = 0
        self._halted = False
        self._price_history: dict[str, list[Decimal]] = {
            symbol: [] for symbol in SUPPORTED_CRYPTO_SPOT_SYMBOLS
        }
        self._last_sample_timestamp: dict[str, int] = {}

    def _daily_orders(self, now_ms: int) -> int:
        day = datetime.fromtimestamp(now_ms / 1000, UTC).date()
        count = 0
        for order in self.service.store.list_orders(self.settings.paper_account_id):
            timestamp = order.get("timestamp_ms")
            if (
                type(timestamp) is int
                and datetime.fromtimestamp(timestamp / 1000, UTC).date() == day
            ):
                count += 1
        return count

    def _daily_pnl(self, now_ms: int) -> Decimal:
        """Reconstruct confirmed daily realized result without treating buys as losses."""
        day = datetime.fromtimestamp(now_ms / 1000, UTC).date()
        total = Decimal(0)
        fills = self.service.store.list_fills(self.settings.paper_account_id)
        for order in self.service.store.list_orders(self.settings.paper_account_id):
            timestamp = order.get("timestamp_ms")
            if type(timestamp) is not int:
                continue
            if datetime.fromtimestamp(timestamp / 1000, UTC).date() != day:
                continue
            if order.get("side") != "spread":
                total += Decimal(str(order.get("realized_pnl_usd", "0")))
                continue
            for fill in fills:
                if fill.get("order_id") != order.get("order_id"):
                    continue
                notional = Decimal(str(fill.get("notional_usd", "0")))
                fee = Decimal(str(fill.get("fee_usd", "0")))
                slippage = Decimal(str(fill.get("slippage_cost_usd", "0")))
                total += notional if fill.get("side") == "sell" else -notional
                total -= fee + slippage
        unrealized = self.service.account(self.settings.paper_account_id).get(
            "strategy_unrealized_pnl_usdt"
        )
        if unrealized is not None:
            total += Decimal(str(unrealized))
        return total

    def _account_gate(self, now_ms: int) -> str | None:
        account = self.service.account(self.settings.paper_account_id)
        if account.get("status") != "active":
            return "paper_robot_halted"
        reconciliation = self.service.reconcile(self.settings.paper_account_id)
        if reconciliation.get("status") != "ok":
            return "reconciliation_mismatch"
        if any(
            order.get("status") in OPEN_STATUSES
            for order in self.service.store.list_orders(self.settings.paper_account_id)
        ):
            return "paper_robot_open_order"
        initial = Decimal(str(account.get("initial_balances", {}).get("USDT", "0")))
        daily_pnl = self._daily_pnl(now_ms)
        if initial > 0 and daily_pnl < 0:
            loss_pct = abs(daily_pnl) / initial * Decimal("100")
            if loss_pct >= self.settings.max_daily_loss_pct:
                return "daily_loss_limit_reached"
        return None

    @staticmethod
    def _symbol_state(
        symbol: str,
        state: str,
        reason_code: str,
        signal: RobotSignal | DirectionalRobotSignal | None = None,
    ) -> dict[str, object]:
        return {
            "symbol": symbol,
            "state": state,
            "reason_code": reason_code,
            "human_reason": human_reason(reason_code),
            "signal": None if signal is None else signal.public(),
        }

    def _record_decision(self, signal: RobotSignal | None, reason_code: str, symbol: str) -> None:
        if self.audit is None:
            return
        fingerprint = repr(
            (
                reason_code,
                None if signal is None else signal.source_timestamp_ms,
                None if signal is None else signal.buy_venue.value,
                None if signal is None else signal.sell_venue.value,
                None if signal is None else signal.net_edge_pct,
            )
        )
        if self._last_decisions.get(symbol) == fingerprint:
            return
        self._last_decisions[symbol] = fingerprint
        self.audit.record(
            "crypto_paper_robot_decision",
            reason_code,
            account_id=self.settings.paper_account_id,
            actor="crypto_paper_robot",
            actor_type="system",
            strategy="bybit_anchored_cross_venue_spread",
            symbol=symbol,
            venue=(
                ""
                if signal is None
                else f"{signal.buy_venue.value}->{signal.sell_venue.value}"
            ),
            market_type="crypto",
            decision=("approved" if signal is not None and signal.risk_approved else "rejected"),
            risk_score=("passed" if signal is not None and signal.risk_approved else "blocked"),
            gross_edge=None if signal is None else signal.gross_edge_pct,
            fees=None if signal is None else signal.fees_pct,
            slippage=None if signal is None else signal.slippage_pct,
            net_edge=None if signal is None else signal.net_edge_pct,
            data_age_ms=None if signal is None else signal.data_age_ms,
            correlation_id=(
                "" if signal is None else f"{symbol}:{signal.source_timestamp_ms}"
            ),
            algorithm_version=self.settings.paper_algorithm_version,
        )

    def _set_all_symbols(self, state: str, reason_code: str) -> None:
        for symbol in SUPPORTED_CRYPTO_SPOT_SYMBOLS:
            self.symbol_states[symbol] = self._symbol_state(symbol, state, reason_code)
            self._record_decision(None, reason_code, symbol)

    def _opportunities(
        self, market_data: PaperMarketData, now_ms: int
    ) -> list[tuple[ArbitrageOpportunity, NormalizedOrderBook, NormalizedOrderBook]]:
        primary = self.settings.crypto_paper_robot_primary_venue
        detector = CrossVenueSpreadMonitor()
        candidates: list[
            tuple[ArbitrageOpportunity, NormalizedOrderBook, NormalizedOrderBook]
        ] = []
        for symbol in SUPPORTED_CRYPTO_SPOT_SYMBOLS:
            primary_book = market_data.book_for_simulation(primary, symbol)
            if primary_book is None:
                continue
            for counter in (Venue.BINANCE, Venue.BYBIT, Venue.OKX):
                if counter == primary:
                    continue
                counter_book = market_data.book_for_simulation(counter, symbol)
                if counter_book is None:
                    continue
                for buy_book, sell_book in (
                    (primary_book, counter_book),
                    (counter_book, primary_book),
                ):
                    try:
                        opportunity = detector.detect(
                            buy_book.to_quote(),
                            sell_book.to_quote(),
                            Decimal("0.20"),
                            Decimal("0.05"),
                        )
                    except ValueError:
                        continue
                    age = now_ms - min(buy_book.timestamp_ms, sell_book.timestamp_ms)
                    if age >= 0:
                        candidates.append((opportunity, buy_book, sell_book))
        return candidates

    def _sample_prices(self, market_data: PaperMarketData, now_ms: int) -> None:
        venue = self.settings.crypto_paper_robot_primary_venue
        for symbol in SUPPORTED_CRYPTO_SPOT_SYMBOLS:
            book = market_data.book_for_simulation(venue, symbol)
            if book is None or not book.bids or not book.asks:
                continue
            age = now_ms - min(book.timestamp_ms, book.received_at_ms)
            if age < 0 or age > self.settings.max_market_data_age_ms:
                continue
            if self._last_sample_timestamp.get(symbol) == book.timestamp_ms:
                continue
            midpoint = (book.bids[0].price + book.asks[0].price) / 2
            history = self._price_history[symbol]
            history.append(midpoint)
            del history[:-240]
            self._last_sample_timestamp[symbol] = book.timestamp_ms

    def _update_marks(self, market_data: PaperMarketData, now_ms: int) -> None:
        venue = self.settings.crypto_paper_robot_primary_venue
        marks: dict[str, Decimal] = {}
        for symbol in SUPPORTED_CRYPTO_SPOT_SYMBOLS:
            book = market_data.book_for_simulation(venue, symbol)
            if book is None:
                continue
            age = now_ms - min(book.timestamp_ms, book.received_at_ms)
            if age < 0 or age > self.settings.max_market_data_age_ms:
                continue
            marks[symbol.split("/")[0]] = (book.bids[0].price + book.asks[0].price) / 2
        if marks:
            self.service.update_marks(
                marks,
                account_id=self.settings.paper_account_id,
                source=f"{venue.value}_public_read_only_midpoint",
            )

    def _record_directional_decision(self, signal: DirectionalRobotSignal) -> None:
        if self.audit is None:
            return
        fingerprint = repr(
            (
                signal.reason_code,
                signal.source_timestamp_ms,
                signal.side.value,
                signal.expected_net_edge_pct,
                signal.validation.closed_trades,
            )
        )
        if self._last_decisions.get(signal.symbol) == fingerprint:
            return
        self._last_decisions[signal.symbol] = fingerprint
        approved = signal.side != SignalSide.HOLD and signal.reason_code == (
            "directional_signal_approved"
        )
        self.audit.record(
            "crypto_paper_robot_decision",
            signal.reason_code,
            account_id=self.settings.paper_account_id,
            actor="crypto_paper_robot",
            actor_type="system",
            strategy="momentum_mean_reversion",
            symbol=signal.symbol,
            venue=signal.venue.value,
            market_type="crypto",
            decision="approved" if approved else "rejected",
            risk_score="passed" if approved else "blocked",
            gross_edge=signal.expected_net_edge_pct
            + self.settings.crypto_paper_directional_fee_pct
            + self.settings.crypto_paper_directional_slippage_pct,
            fees=self.settings.crypto_paper_directional_fee_pct,
            slippage=self.settings.crypto_paper_directional_slippage_pct,
            net_edge=signal.expected_net_edge_pct,
            data_age_ms=signal.data_age_ms,
            correlation_id=f"{signal.symbol}:{signal.source_timestamp_ms}",
            algorithm_version=self.settings.paper_algorithm_version,
        )

    def _directional_candidate(
        self,
        market_data: PaperMarketData,
        symbol: str,
        now_ms: int,
    ) -> tuple[DirectionalRobotSignal, Decimal | None, NormalizedOrderBook | None]:
        venue = self.settings.crypto_paper_robot_primary_venue
        book = market_data.book_for_simulation(venue, symbol)
        history = tuple(self._price_history[symbol])
        base_signal: DirectionalSignal = directional_signal(
            history,
            fee_pct=self.settings.crypto_paper_directional_fee_pct,
            slippage_pct=self.settings.crypto_paper_directional_slippage_pct,
        )
        validation = walk_forward(
            history,
            fee_pct=self.settings.crypto_paper_directional_fee_pct,
            slippage_pct=self.settings.crypto_paper_directional_slippage_pct,
        )
        timestamp_ms = 0 if book is None else book.timestamp_ms
        age = self.settings.max_market_data_age_ms + 1 if book is None else (
            now_ms - min(book.timestamp_ms, book.received_at_ms)
        )
        reason = base_signal.reason_code
        quantity: Decimal | None = None
        account = self.service.account(self.settings.paper_account_id)
        base_asset = symbol.split("/")[0]
        owned = Decimal(
            str(account.get("strategy_inventory", {}).get(base_asset, {}).get("quantity", "0"))
        )
        rules = market_data.rules_for_simulation(venue, symbol)
        if book is None:
            reason = "source_unavailable"
        elif age < 0 or age > self.settings.max_market_data_age_ms:
            reason = "stale_market_data"
        elif base_signal.side == SignalSide.BUY and owned > 0:
            reason = "strategy_position_open"
        elif base_signal.side == SignalSide.SELL and owned <= 0:
            reason = "strategy_position_unavailable"
        elif base_signal.side == SignalSide.BUY and not validation.approved(
            min_closed_trades=self.settings.crypto_paper_directional_min_closed_trades,
            min_average_trade_net_pct=self.settings.min_expected_net_pct,
            max_drawdown_pct=self.settings.crypto_paper_directional_max_drawdown_pct,
        ):
            reason = "strategy_validation_failed"
        elif (
            base_signal.side != SignalSide.HOLD
            and base_signal.expected_net_edge_pct < self.settings.min_expected_net_pct
        ):
            reason = "insufficient_net_edge"
        elif base_signal.side != SignalSide.HOLD and (book is None or rules is None):
            reason = "instrument_rules_unavailable"
        elif base_signal.side != SignalSide.HOLD and book is not None and rules is not None:
            rules.validate()
            raw_quantity = (
                self.settings.crypto_paper_robot_notional_usdt / book.asks[0].price
                if base_signal.side == SignalSide.BUY
                else owned
            )
            quantity = (
                raw_quantity / rules.quantity_step
            ).to_integral_value(rounding=ROUND_DOWN) * rules.quantity_step
            price = book.asks[0].price if base_signal.side == SignalSide.BUY else book.bids[0].price
            if quantity < rules.min_quantity:
                reason, quantity = "instrument_min_quantity", None
            elif rules.min_notional is not None and quantity * price < rules.min_notional:
                reason, quantity = "instrument_min_notional", None
            else:
                reason = "directional_signal_approved"
        signal = DirectionalRobotSignal(
            symbol,
            venue,
            base_signal.side,
            base_signal.reference_price,
            base_signal.momentum_pct,
            base_signal.deviation_pct,
            base_signal.expected_net_edge_pct,
            timestamp_ms,
            max(0, age),
            reason,
            validation,
        )
        return signal, quantity, book

    def _run_directional(
        self, market_data: PaperMarketData, timestamp: int
    ) -> dict[str, object]:
        candidates: list[
            tuple[DirectionalRobotSignal, Decimal, NormalizedOrderBook]
        ] = []
        evaluated_signals: list[DirectionalRobotSignal] = []
        for symbol in SUPPORTED_CRYPTO_SPOT_SYMBOLS:
            signal, quantity, book = self._directional_candidate(
                market_data, symbol, timestamp
            )
            evaluated_signals.append(signal)
            ready = (
                quantity is not None
                and book is not None
                and signal.reason_code == "directional_signal_approved"
            )
            self.symbol_states[symbol] = self._symbol_state(
                symbol, "ready" if ready else "scanning", signal.reason_code, signal
            )
            self._record_directional_decision(signal)
            if ready:
                assert quantity is not None and book is not None
                candidates.append((signal, quantity, book))
        if not candidates:
            self.last_reason_code = "strategy_no_signal"
            self.state = "scanning"
            if evaluated_signals:
                best = max(
                    evaluated_signals, key=lambda item: item.expected_net_edge_pct
                )
                self.last_signal = best
                self.last_reason_code = best.reason_code
            return self.snapshot()
        candidates.sort(key=lambda item: item[0].expected_net_edge_pct, reverse=True)
        signal, quantity, book = candidates[0]
        self.last_signal = signal
        fingerprint = repr(
            (
                signal.symbol,
                signal.venue.value,
                signal.side.value,
                signal.source_timestamp_ms,
                str(quantity),
            )
        )
        result = self.service.execute_spot(
            symbol=signal.symbol,
            venue=signal.venue,
            side=signal.side.value,
            book=book,
            quantity=quantity,
            fee_rate_pct=self.settings.crypto_paper_directional_fee_pct,
            slippage_pct=self.settings.crypto_paper_directional_slippage_pct,
            expected_net_edge_pct=signal.expected_net_edge_pct,
            settings=self.settings,
            idempotency_key="crypto-directional:" + sha256(fingerprint.encode()).hexdigest(),
            account_id=self.settings.paper_account_id,
        )
        self.last_result = {
            "order_id": result.get("order_id"),
            "status": result.get("status"),
            "reason_code": result.get("reason_code"),
            "symbol": signal.symbol,
            "side": signal.side.value,
        }
        status = str(result.get("status", ""))
        self.state = "filled" if status == "filled" else "scanning"
        self.last_reason_code = str(result.get("reason_code", "paper_robot_halted"))
        self.symbol_states[signal.symbol] = self._symbol_state(
            signal.symbol, self.state, self.last_reason_code, signal
        )
        return self.snapshot()

    def _signal(
        self,
        market_data: PaperMarketData,
        opportunity: ArbitrageOpportunity,
        buy_book: NormalizedOrderBook,
        sell_book: NormalizedOrderBook,
        now_ms: int,
    ) -> tuple[RobotSignal, Decimal | None]:
        age = now_ms - min(buy_book.timestamp_ms, sell_book.timestamp_ms)
        risk = RiskEngine(
            RiskLimits(
                self.settings.max_daily_loss_pct,
                self.settings.max_trade_notional_usd,
                self.settings.min_expected_net_pct,
            )
        ).evaluate(
            replace(
                opportunity,
                max_notional_usd=self.settings.crypto_paper_robot_notional_usdt,
            ),
            data_age_ms=age,
            max_data_age_ms=self.settings.max_market_data_age_ms,
        )
        reason_code = risk.reason_code
        executable_notional: Decimal | None = None
        if risk.approved:
            buy_rules = market_data.rules_for_simulation(buy_book.venue, opportunity.symbol)
            sell_rules = market_data.rules_for_simulation(sell_book.venue, opportunity.symbol)
            if buy_rules is None or sell_rules is None:
                reason_code = "instrument_rules_unavailable"
                risk = RiskDecision(
                    False,
                    "Public instrument rules are unavailable",
                    reason_code=reason_code,
                )
            else:
                sizing = size_paper_spread(
                    self.settings.crypto_paper_robot_notional_usdt,
                    buy_price=buy_book.asks[0].price,
                    sell_price=sell_book.bids[0].price,
                    buy_available=buy_book.asks[0].quantity,
                    sell_available=sell_book.bids[0].quantity,
                    buy_rules=buy_rules,
                    sell_rules=sell_rules,
                )
                reason_code = sizing.reason_code
                if sizing.approved:
                    executable_notional = sizing.buy_notional
                else:
                    risk = RiskDecision(False, reason_code, reason_code=reason_code)
        signal = RobotSignal(
            opportunity.symbol,
            opportunity.buy_exchange,
            opportunity.sell_exchange,
            opportunity.expected_gross_pct,
            opportunity.fees_pct,
            opportunity.slippage_pct,
            opportunity.expected_net_pct,
            min(buy_book.timestamp_ms, sell_book.timestamp_ms),
            max(0, age),
            risk.approved,
            reason_code,
            executable_notional or Decimal(0),
        )
        return signal, executable_notional

    def run_once(
        self,
        market_data: PaperMarketData,
        *,
        now_ms: int | None = None,
    ) -> dict[str, object]:
        timestamp = int(time() * 1000) if now_ms is None else now_ms
        self.last_checked_at = datetime.fromtimestamp(timestamp / 1000, UTC).isoformat()
        if not self.settings.crypto_paper_robot_enabled:
            self.state, self.last_reason_code = "disabled", "paper_robot_disabled"
            self._set_all_symbols("disabled", "paper_robot_disabled")
            return self.snapshot()
        if (
            self.settings.trading_mode != TradingMode.PAPER
            or self.settings.live_trading_enabled
            or self.settings.live_order_acceptance_gate
        ):
            self._halted = True
            self.state, self.last_reason_code = "halted", "live_trading_locked"
            self._set_all_symbols("halted", "live_trading_locked")
            return self.snapshot()
        if self._halted:
            self.state, self.last_reason_code = "halted", "paper_robot_halted"
            self._set_all_symbols("halted", "paper_robot_halted")
            return self.snapshot()
        self._sample_prices(market_data, timestamp)
        if (
            timestamp - self._last_attempt_ms
            < self.settings.crypto_paper_robot_interval_seconds * 1000
        ):
            return self.snapshot()
        self._last_attempt_ms = timestamp
        self._update_marks(market_data, timestamp)
        gate = self._account_gate(timestamp)
        if gate is not None:
            self.state = (
                "halted"
                if gate in ("paper_robot_halted", "reconciliation_mismatch")
                else "paused"
            )
            self.last_reason_code = gate
            self._set_all_symbols(self.state, gate)
            return self.snapshot()
        if self._daily_orders(timestamp) >= self.settings.crypto_paper_robot_max_orders_per_day:
            self.state, self.last_reason_code = "paused", "paper_robot_rate_limit"
            self._set_all_symbols("paused", "paper_robot_rate_limit")
            return self.snapshot()
        try:
            candidates = self._opportunities(market_data, timestamp)
            if not candidates:
                if self.settings.crypto_paper_directional_enabled:
                    return self._run_directional(market_data, timestamp)
                self.state, self.last_reason_code = "paused", "source_unavailable"
                self._set_all_symbols("paused", "source_unavailable")
                return self.snapshot()
            evaluated: list[
                tuple[RobotSignal, Decimal | None, ArbitrageOpportunity,
                      NormalizedOrderBook, NormalizedOrderBook]
            ] = []
            for symbol in SUPPORTED_CRYPTO_SPOT_SYMBOLS:
                choices = [item for item in candidates if item[0].symbol == symbol]
                if not choices:
                    self.symbol_states[symbol] = self._symbol_state(
                        symbol, "paused", "source_unavailable"
                    )
                    self._record_decision(None, "source_unavailable", symbol)
                    continue
                choices.sort(key=lambda item: item[0].expected_net_pct, reverse=True)
                opportunity, buy_book, sell_book = choices[0]
                signal, notional = self._signal(
                    market_data, opportunity, buy_book, sell_book, timestamp
                )
                pair_state = "ready" if signal.risk_approved else "scanning"
                self.symbol_states[symbol] = self._symbol_state(
                    symbol, pair_state, signal.reason_code, signal
                )
                self._record_decision(signal, signal.reason_code, symbol)
                evaluated.append((signal, notional, opportunity, buy_book, sell_book))
            approved = [item for item in evaluated if item[0].risk_approved and item[1] is not None]
            if not approved:
                if self.settings.crypto_paper_directional_enabled:
                    return self._run_directional(market_data, timestamp)
                best = max(evaluated, key=lambda item: item[0].net_edge_pct)
                self.last_signal = best[0]
                self.last_reason_code = best[0].reason_code
                self.state = "scanning"
                return self.snapshot()
            approved.sort(key=lambda item: item[0].net_edge_pct, reverse=True)
            signal, notional, opportunity, buy_book, sell_book = approved[0]
            assert notional is not None
            self.last_signal = signal
            self.last_reason_code = signal.reason_code
            fingerprint = repr(
                (
                    signal.symbol,
                    signal.buy_venue.value,
                    signal.sell_venue.value,
                    signal.source_timestamp_ms,
                    str(signal.net_edge_pct),
                    str(notional),
                )
            )
            key = "crypto-robot:" + sha256(fingerprint.encode()).hexdigest()
            result = self.service.execute(
                opportunity,
                buy_book,
                sell_book,
                notional_usd=notional,
                settings=self.settings,
                idempotency_key=key,
                account_id=self.settings.paper_account_id,
                actor="crypto_paper_robot",
            )
            self.last_result = {
                "order_id": result.get("order_id"),
                "status": result.get("status"),
                "reason_code": result.get("reason_code"),
                "symbol": signal.symbol,
            }
            result_status = str(result.get("status", ""))
            self.state = (
                "filled"
                if result_status == "filled"
                else "needs_review"
                if result_status == "partially_filled"
                else "scanning"
                if result_status == "rejected"
                else "halted"
            )
            self.last_reason_code = str(result.get("reason_code", "paper_robot_halted"))
            self.symbol_states[signal.symbol] = self._symbol_state(
                signal.symbol, self.state, self.last_reason_code, signal
            )
            return self.snapshot()
        except Exception:
            self._halted = True
            self.state, self.last_reason_code = "halted", "paper_robot_halted"
            self._set_all_symbols("halted", "paper_robot_halted")
            return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state,
            "reason_code": self.last_reason_code,
            "enabled": self.settings.crypto_paper_robot_enabled,
            "paper_only": True,
            "live_execution": False,
            "primary_venue": self.settings.crypto_paper_robot_primary_venue.value,
            "strategy": (
                "cross_venue_spread+momentum_mean_reversion"
                if self.settings.crypto_paper_directional_enabled
                else "bybit_anchored_cross_venue_spread"
            ),
            "directional_enabled": self.settings.crypto_paper_directional_enabled,
            "symbols": list(SUPPORTED_CRYPTO_SPOT_SYMBOLS),
            "notional_limit_usdt": str(self.settings.crypto_paper_robot_notional_usdt),
            "max_orders_per_day": self.settings.crypto_paper_robot_max_orders_per_day,
            "last_checked_at": self.last_checked_at,
            "last_signal": None if self.last_signal is None else self.last_signal.public(),
            "last_result": self.last_result,
            "symbol_states": [
                self.symbol_states[symbol] for symbol in SUPPORTED_CRYPTO_SPOT_SYMBOLS
            ],
        }
