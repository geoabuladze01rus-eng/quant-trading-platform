from dataclasses import dataclass
from decimal import Decimal
from time import time

from quant_trading_platform.models import ArbitrageOpportunity, venue_matches_market


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskLimits:
    max_daily_loss_pct: Decimal = Decimal("2")
    max_trade_notional_usd: Decimal = Decimal("100")
    min_expected_net_pct: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        for limit in (
            self.max_daily_loss_pct,
            self.max_trade_notional_usd,
            self.min_expected_net_pct,
        ):
            if not limit.is_finite() or limit <= 0:
                raise ValueError("Risk limits must be finite and positive")
        if self.max_daily_loss_pct > 2:
            raise ValueError("Daily loss limit must not exceed 2%")


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    def evaluate(
        self,
        opportunity: ArbitrageOpportunity,
        *,
        daily_loss_pct: Decimal = Decimal("0"),
        data_age_ms: int | None = None,
        max_data_age_ms: int = 1_000,
        api_error: bool = False,
        balance_mismatch: bool = False,
    ) -> RiskDecision:
        if api_error:
            return RiskDecision(False, "API error: trading paused")
        if balance_mismatch:
            return RiskDecision(False, "Balance mismatch: trading stopped")
        source_timestamp = opportunity.source_timestamp_ms
        if source_timestamp is None:
            source_timestamp = opportunity.detected_at_ms
        source_age_ms = int(time() * 1000) - source_timestamp
        if source_timestamp < 0 or source_age_ms < 0:
            return RiskDecision(False, "Invalid market data timestamp")
        if data_age_ms is None:
            data_age_ms = source_age_ms
        elif data_age_ms < 0:
            return RiskDecision(False, "Invalid market data age")
        else:
            data_age_ms = max(data_age_ms, source_age_ms)
        if max_data_age_ms <= 0:
            return RiskDecision(False, "Invalid market data age limit")
        if data_age_ms > max_data_age_ms:
            return RiskDecision(False, "Market data is stale")
        if not daily_loss_pct.is_finite() or daily_loss_pct < 0:
            return RiskDecision(False, "Invalid daily loss")
        values = (
            opportunity.expected_gross_pct,
            opportunity.expected_net_pct,
            opportunity.max_notional_usd,
            opportunity.fees_pct,
            opportunity.slippage_pct,
        )
        if not all(value.is_finite() for value in values):
            return RiskDecision(False, "Invalid opportunity numeric data")
        if opportunity.fees_pct < 0 or opportunity.slippage_pct < 0:
            return RiskDecision(False, "Invalid fees or slippage")
        net_after_costs = (
            opportunity.expected_gross_pct - opportunity.fees_pct - opportunity.slippage_pct
        )
        if opportunity.expected_net_pct > net_after_costs:
            return RiskDecision(False, "Expected net edge does not account for costs")
        if opportunity.max_notional_usd <= 0:
            return RiskDecision(False, "Trade notional must be positive")
        if not all(
            venue_matches_market(venue, opportunity.market_type)
            for venue in (opportunity.buy_exchange, opportunity.sell_exchange)
        ):
            return RiskDecision(False, "Venue and market type mismatch")
        if opportunity.rejection_reason:
            return RiskDecision(False, opportunity.rejection_reason)
        if daily_loss_pct >= self._limits.max_daily_loss_pct:
            return RiskDecision(False, "Daily loss limit reached")
        if not opportunity.is_profitable:
            return RiskDecision(False, "Expected net profit is not positive")

        if opportunity.expected_net_pct < self._limits.min_expected_net_pct:
            return RiskDecision(False, "Expected net profit is below minimum threshold")

        if opportunity.max_notional_usd > self._limits.max_trade_notional_usd:
            return RiskDecision(False, "Opportunity exceeds per-trade notional limit")

        return RiskDecision(True, "Approved for paper-trading simulation", ("paper_only",))
