"""Thin strategy coordinator over the canonical persistent paper service."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from time import time
from typing import Protocol

from quant_trading_platform.audit_log import PersistentAuditLog
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
        self.last_signal: RobotSignal | None = None
        self.last_result: dict[str, object] | None = None
        self.symbol_states: dict[str, dict[str, object]] = {
            symbol: self._symbol_state(symbol, "disabled", "paper_robot_disabled")
            for symbol in SUPPORTED_CRYPTO_SPOT_SYMBOLS
        }
        self._last_decisions: dict[str, str] = {}
        self._last_attempt_ms = 0
        self._halted = False

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
        """Reconstruct today's confirmed spread cash result from durable fills."""
        day = datetime.fromtimestamp(now_ms / 1000, UTC).date()
        total = Decimal(0)
        for fill in self.service.store.list_fills(self.settings.paper_account_id):
            timestamp = fill.get("timestamp_ms")
            if type(timestamp) is not int:
                continue
            if datetime.fromtimestamp(timestamp / 1000, UTC).date() != day:
                continue
            notional = Decimal(str(fill.get("notional_usd", "0")))
            fee = Decimal(str(fill.get("fee_usd", "0")))
            slippage = Decimal(str(fill.get("slippage_cost_usd", "0")))
            total += notional if fill.get("side") == "sell" else -notional
            total -= fee + slippage
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
        signal: RobotSignal | None = None,
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
        if (
            timestamp - self._last_attempt_ms
            < self.settings.crypto_paper_robot_interval_seconds * 1000
        ):
            return self.snapshot()
        self._last_attempt_ms = timestamp
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
            "strategy": "bybit_anchored_cross_venue_spread",
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
