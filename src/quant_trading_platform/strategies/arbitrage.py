from decimal import Decimal
from time import time

from quant_trading_platform.models import ArbitrageOpportunity, MarketQuote


def _opportunity(
    strategy: str,
    symbol: str,
    buy: MarketQuote,
    sell: MarketQuote,
    fees_pct: Decimal,
    slippage_pct: Decimal,
) -> ArbitrageOpportunity:
    gross = (sell.bid - buy.ask) / buy.ask * Decimal("100")
    net = gross - fees_pct - slippage_pct
    return ArbitrageOpportunity(
        strategy=strategy,
        symbol=symbol,
        buy_exchange=buy.venue,
        sell_exchange=sell.venue,
        expected_gross_pct=gross,
        expected_net_pct=net,
        gross_spread_pct=gross,
        fees_pct=fees_pct,
        slippage_pct=slippage_pct,
        max_notional_usd=min(buy.ask_size, sell.bid_size) * buy.ask,
        detected_at_ms=int(time() * 1000),
        rejection_reason=None if net > 0 else "Expected net edge is not positive",
    )


class CrossVenueSpreadMonitor:
    def detect(
        self, buy: MarketQuote, sell: MarketQuote, fees_pct: Decimal, slippage_pct: Decimal
    ) -> ArbitrageOpportunity:
        return _opportunity("cross_venue_spread", buy.symbol, buy, sell, fees_pct, slippage_pct)


class TriangularArbitrageDetector:
    """Mock triangular detector; quote ordering represents the synthetic route."""

    def detect(
        self, entry: MarketQuote, exit: MarketQuote, fees_pct: Decimal, slippage_pct: Decimal
    ) -> ArbitrageOpportunity:
        return _opportunity(
            "triangular_arbitrage", entry.symbol, entry, exit, fees_pct, slippage_pct
        )
