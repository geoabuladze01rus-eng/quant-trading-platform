"""In-memory, atomic spread simulation. No venue execution dependencies."""

from collections import deque
from dataclasses import dataclass, replace
from decimal import Decimal
from time import time
from uuid import uuid4

from quant_trading_platform.config import MarketScope, Settings, TradingMode
from quant_trading_platform.market_data.models import (
    NormalizedOrderBook,
    StaleMarketDataError,
    normalize_order_book,
)
from quant_trading_platform.models import ArbitrageOpportunity, MarketType, Venue, normalize_symbol
from quant_trading_platform.risk import RiskEngine, RiskLimits


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    execution_id: str
    venue: Venue
    symbol: str
    side: str
    quantity: Decimal
    timestamp_ms: int
    status: str = "filled"


@dataclass(frozen=True)
class PaperFill:
    fill_id: str
    order_id: str
    execution_id: str
    venue: Venue
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    notional_usd: Decimal
    fee_usd: Decimal
    timestamp_ms: int
    reason_code: str
    reason_text: str


@dataclass(frozen=True)
class DepthFill:
    """Single-leg depth result used by the canonical execution lifecycle."""

    requested_qty: Decimal
    filled_qty: Decimal
    remaining_qty: Decimal
    average_fill_price: Decimal
    fee: Decimal
    slippage: Decimal
    status: str


@dataclass(frozen=True)
class PaperPosition:
    """Flat hypothetical paired trade; this is not a funded account cash ledger."""

    execution_id: str
    symbol: str
    quantity: Decimal
    realized_pnl_usd: Decimal


@dataclass(frozen=True)
class Reconciliation:
    """Fills use depth VWAP; reserve cost is deducted separately from their net cash flow."""

    expected_net_pct: Decimal
    simulated_net_pct: Decimal | None
    fees_pct: Decimal
    slippage_pct: Decimal
    data_age_ms: int | None
    decision_reason: str
    execution_status: str
    slippage_cost_usd: Decimal
    depth_slippage_pct: Decimal


@dataclass(frozen=True)
class PaperExecutionReport:
    execution_id: str
    status: str
    reason_code: str
    reason_text: str
    orders: tuple[PaperOrder, ...]
    fills: tuple[PaperFill, ...]
    reconciliation: Reconciliation


class PaperExecutionEngine:
    """All-or-reject hypothetical paired fills, without inventory or settlement."""

    def __init__(self, *, max_records: int = 1000) -> None:
        if type(max_records) is not int or max_records <= 0:
            raise ValueError("max_records must be a positive integer")
        self._reports: deque[PaperExecutionReport] = deque(maxlen=max_records)
        self._positions: deque[PaperPosition] = deque(maxlen=max_records)

    @property
    def reports(self) -> tuple[PaperExecutionReport, ...]:
        return tuple(self._reports)

    @property
    def orders(self) -> tuple[PaperOrder, ...]:
        return tuple(order for report in self._reports for order in report.orders)

    @property
    def fills(self) -> tuple[PaperFill, ...]:
        return tuple(fill for report in self._reports for fill in report.fills)

    @property
    def positions(self) -> tuple[PaperPosition, ...]:
        return tuple(self._positions)

    def simulate(
        self,
        opportunity: ArbitrageOpportunity,
        buy_book: NormalizedOrderBook | None,
        sell_book: NormalizedOrderBook | None,
        *,
        notional_usd: Decimal,
        settings: Settings,
    ) -> PaperExecutionReport:
        execution_id = str(uuid4())
        age: int | None = None
        net: Decimal | None = None
        fees_pct = opportunity.fees_pct if opportunity.fees_pct.is_finite() else Decimal(0)
        slippage_pct = (
            opportunity.slippage_pct if opportunity.slippage_pct.is_finite() else Decimal(0)
        )
        expected_net = Decimal(0)
        slippage_cost = Decimal(0)
        depth_slippage_pct = Decimal(0)

        def finish(
            code: str,
            text: str,
            orders: tuple[PaperOrder, ...] = (),
            fills: tuple[PaperFill, ...] = (),
        ) -> PaperExecutionReport:
            status = "filled" if fills else "rejected"
            report = PaperExecutionReport(
                execution_id,
                status,
                code,
                text,
                orders,
                fills,
                Reconciliation(
                    expected_net,
                    net,
                    fees_pct,
                    slippage_pct,
                    age,
                    text,
                    status,
                    slippage_cost,
                    depth_slippage_pct,
                ),
            )
            self._reports.append(report)
            return report

        if settings.trading_mode != TradingMode.PAPER or settings.live_trading_enabled:
            return finish(
                "live_trading_locked", "Simulation requires paper mode with live disabled"
            )
        if (
            settings.market_scope != MarketScope.MIXED
            and settings.market_scope.value != opportunity.market_type.value
        ):
            return finish("market_type_mismatch", "Opportunity is outside configured market scope")
        try:
            symbol = normalize_symbol(opportunity.symbol)
        except ValueError:
            return finish("market_type_mismatch", "Invalid paper spread symbol")
        if opportunity.market_type != MarketType.CRYPTO or symbol.rsplit("/", 1)[-1] not in (
            "USD",
            "USDT",
            "USDC",
        ):
            return finish("market_type_mismatch", "Paper spreads require a USD-quoted crypto pair")
        if (
            any(
                not value.is_finite()
                for value in (
                    opportunity.fees_pct,
                    opportunity.slippage_pct,
                    opportunity.expected_net_pct,
                    opportunity.expected_gross_pct,
                )
            )
            or fees_pct < 0
            or slippage_pct < 0
        ):
            return finish(
                "invalid_opportunity", "Opportunity numbers must be finite; costs nonnegative"
            )
        if not notional_usd.is_finite() or notional_usd <= 0:
            return finish("invalid_notional", "Requested notional must be finite and positive")
        if (
            not opportunity.max_notional_usd.is_finite()
            or notional_usd > opportunity.max_notional_usd
        ):
            return finish("notional_limit_exceeded", "Requested size exceeds opportunity capacity")
        if buy_book is None or sell_book is None:
            return finish("venue_unavailable", "Both order books are required")
        now = int(time() * 1000)
        for book, venue in (
            (buy_book, opportunity.buy_exchange),
            (sell_book, opportunity.sell_exchange),
        ):
            try:
                if (
                    book.venue != venue
                    or book.market_type != opportunity.market_type
                    or normalize_symbol(book.symbol) != normalize_symbol(opportunity.symbol)
                    or opportunity.buy_exchange == opportunity.sell_exchange
                ):
                    return finish(
                        "market_type_mismatch", "Book identity does not match spread legs"
                    )
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
                if book.received_at_ms > now:
                    return finish("invalid_timestamp", "Order book receipt time is in the future")
            except StaleMarketDataError as error:
                return finish("stale_market_data", str(error))
            except ValueError as error:
                return finish("invalid_book", str(error))
        age = now - min(buy_book.timestamp_ms, sell_book.timestamp_ms)
        top_buy = min(level.price for level in buy_book.asks)
        top_sell = max(level.price for level in sell_book.bids)
        top_gross_pct = (top_sell / top_buy - 1) * 100
        expected_net = top_gross_pct - fees_pct - slippage_pct
        risk = RiskEngine(
            RiskLimits(
                max_daily_loss_pct=Decimal(str(settings.max_daily_loss_pct)),
                max_trade_notional_usd=Decimal(str(settings.max_trade_notional_usd)),
                min_expected_net_pct=Decimal(str(settings.min_expected_net_pct)),
            )
        ).evaluate(
            replace(
                opportunity,
                max_notional_usd=notional_usd,
                expected_gross_pct=top_gross_pct,
                expected_net_pct=expected_net,
            ),
            data_age_ms=age,
            max_data_age_ms=settings.max_market_data_age_ms,
        )
        if not risk.approved:
            return finish(getattr(risk, "reason_code", "risk_rejected"), risk.reason)
        # Consume the best asks with the requested quote budget, then identical base on bids.
        remaining = notional_usd
        quantity = Decimal(0)
        for level in sorted(buy_book.asks, key=lambda item: item.price):
            cost = min(remaining, level.price * level.quantity)
            quantity += cost / level.price
            remaining -= cost
            if remaining == 0:
                break
        if remaining > 0:
            return finish("insufficient_depth", "Buy order book cannot fill requested size")
        remaining_quantity = quantity
        proceeds = Decimal(0)
        for level in sorted(sell_book.bids, key=lambda item: item.price, reverse=True):
            size = min(remaining_quantity, level.quantity)
            proceeds += size * level.price
            remaining_quantity -= size
            if remaining_quantity == 0:
                break
        if remaining_quantity > 0:
            return finish("insufficient_depth", "Sell order book cannot fill the paired quantity")
        leg_fee_rate = opportunity.fees_pct / Decimal(200)
        buy_fee, sell_fee = notional_usd * leg_fee_rate, proceeds * leg_fee_rate
        fees_pct = (buy_fee + sell_fee) / notional_usd * 100
        slippage_cost = notional_usd * opportunity.slippage_pct / 100
        actual_gross_pct = (proceeds / notional_usd - 1) * 100
        depth_slippage_pct = max(Decimal(0), top_gross_pct - actual_gross_pct)
        slippage_pct += depth_slippage_pct
        pnl = proceeds - notional_usd - buy_fee - sell_fee - slippage_cost
        net = pnl / notional_usd * 100
        if net < Decimal(str(settings.min_expected_net_pct)):
            return finish(
                "insufficient_edge_after_costs", "Depth and costs reduce edge below minimum"
            )
        orders = tuple(
            PaperOrder(str(uuid4()), execution_id, venue, opportunity.symbol, side, quantity, now)
            for venue, side in ((buy_book.venue, "buy"), (sell_book.venue, "sell"))
        )
        fills = tuple(
            PaperFill(
                str(uuid4()),
                order.order_id,
                execution_id,
                order.venue,
                order.symbol,
                order.side,
                quantity,
                value / quantity,
                value,
                fee,
                now,
                "paper_filled",
                "Hypothetical depth-weighted fill; fees charged and paired-leg risk checks passed",
            )
            for order, value, fee in zip(
                orders, (notional_usd, proceeds), (buy_fee, sell_fee), strict=True
            )
        )
        self._positions.append(PaperPosition(execution_id, opportunity.symbol, Decimal(0), pnl))
        return finish(
            "paper_filled", "Both spread legs simulated after risk and depth checks", orders, fills
        )

    def simulate_depth_fill(
        self,
        order_book: NormalizedOrderBook,
        *,
        side: str,
        quantity: Decimal,
        expected_price: Decimal,
        fee_rate_pct: Decimal = Decimal("0.1"),
    ) -> DepthFill:
        """Consume one normalized book side without ever contacting a venue.

        Unlike :meth:`simulate`, this method deliberately returns partial and
        zero fills. The orchestrator owns lifecycle state, idempotency, balance
        accounting and protective hedging around these deterministic results.
        """
        if side not in ("buy", "sell"):
            raise ValueError("Paper order side must be buy or sell")
        for name, value in (
            ("quantity", quantity),
            ("expected_price", expected_price),
            ("fee_rate_pct", fee_rate_pct),
        ):
            if not value.is_finite() or value < 0 or (name != "fee_rate_pct" and value == 0):
                raise ValueError(f"{name} must be finite and positive")
        levels = order_book.asks if side == "buy" else order_book.bids
        remaining = quantity
        notional = Decimal(0)
        for level in levels:
            taken = min(remaining, level.quantity)
            notional += taken * level.price
            remaining -= taken
            if remaining == 0:
                break
        filled = quantity - remaining
        if filled == 0:
            return DepthFill(
                quantity,
                Decimal(0),
                quantity,
                Decimal(0),
                Decimal(0),
                Decimal(0),
                "unfilled",
            )
        average = notional / filled
        direction = Decimal(1) if side == "buy" else Decimal(-1)
        slippage = direction * (average - expected_price) / expected_price * 100
        return DepthFill(
            quantity,
            filled,
            remaining,
            average,
            notional * fee_rate_pct / 100,
            slippage,
            "filled" if remaining == 0 else "partially_filled",
        )


# Compatibility for callers of the first in-memory implementation.
PaperEngine = PaperExecutionEngine
ExecutionReport = PaperExecutionReport
