from dataclasses import dataclass
from decimal import Decimal

from quant_trading_platform.models import ArbitrageOpportunity


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


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    def evaluate(
        self,
        opportunity: ArbitrageOpportunity,
        *,
        daily_loss_pct: Decimal = Decimal("0"),
        data_age_ms: int = 0,
        max_data_age_ms: int = 1_000,
        api_error: bool = False,
        balance_mismatch: bool = False,
    ) -> RiskDecision:
        if api_error:
            return RiskDecision(False, "API error: trading paused")
        if balance_mismatch:
            return RiskDecision(False, "Balance mismatch: trading stopped")
        if data_age_ms > max_data_age_ms:
            return RiskDecision(False, "Market data is stale")
        if daily_loss_pct >= self._limits.max_daily_loss_pct:
            return RiskDecision(False, "Daily loss limit reached")
        if not opportunity.is_profitable:
            return RiskDecision(False, "Expected net profit is not positive")

        if opportunity.expected_net_pct < self._limits.min_expected_net_pct:
            return RiskDecision(False, "Expected net profit is below minimum threshold")

        if opportunity.max_notional_usd > self._limits.max_trade_notional_usd:
            return RiskDecision(False, "Opportunity exceeds per-trade notional limit")

        return RiskDecision(True, "Approved for paper-trading simulation", ("paper_only",))
