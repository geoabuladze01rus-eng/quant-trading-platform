from decimal import Decimal
from time import time

from quant_trading_platform.models import (
    ArbitrageOpportunity,
    MarketQuote,
    MarketType,
    normalize_symbol,
)


def _opportunity(
    strategy: str,
    symbol: str,
    buy: MarketQuote,
    sell: MarketQuote,
    fees_pct: Decimal,
    slippage_pct: Decimal,
) -> ArbitrageOpportunity:
    buy.validate()
    sell.validate()
    symbol = normalize_symbol(symbol)
    if normalize_symbol(buy.symbol) != normalize_symbol(sell.symbol):
        raise ValueError("Symbol mismatch between quote legs")
    if buy.market_type != sell.market_type:
        raise ValueError("Market type mismatch between quote legs")
    if buy.market_type != MarketType.CRYPTO:
        raise ValueError("Crypto detectors cannot use Russian market quotes")
    if "/" not in symbol or symbol.split("/")[1] not in ("USD", "USDT", "USDC"):
        raise ValueError("USD notional requires a USD-denominated quote pair")
    for cost in (fees_pct, slippage_pct):
        if not cost.is_finite() or cost < 0:
            raise ValueError("Fees and slippage must be finite and non-negative")
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
        market_type=buy.market_type,
        source_timestamp_ms=min(buy.timestamp_ms, sell.timestamp_ms),
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
