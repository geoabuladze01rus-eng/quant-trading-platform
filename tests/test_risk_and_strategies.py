from decimal import Decimal

from quant_trading_platform.models import MarketQuote, Venue
from quant_trading_platform.risk import RiskEngine, RiskLimits
from quant_trading_platform.strategies import CrossVenueSpreadMonitor


def _quote(venue: Venue, bid: str, ask: str) -> MarketQuote:
    return MarketQuote(venue, "BTC/USDT", Decimal(bid), Decimal(ask), Decimal("1"), Decimal("1"), 0)


def test_spread_monitor_calculates_net_edge_and_risk_rejects_stale_data() -> None:
    opportunity = CrossVenueSpreadMonitor().detect(
        _quote(Venue.OKX, "100", "100"),
        _quote(Venue.BYBIT, "101", "101"),
        Decimal("0.10"),
        Decimal("0.05"),
    )
    assert opportunity.gross_spread_pct == Decimal("1.00")
    assert opportunity.expected_net_pct == Decimal("0.85")
    decision = RiskEngine(RiskLimits()).evaluate(opportunity, data_age_ms=1_001)
    assert decision.approved is False
    assert decision.reason == "Market data is stale"
