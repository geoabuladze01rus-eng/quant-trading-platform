"""Explicit paper-only crypto spot universe; quote currencies are never conflated."""

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from math import gcd

from quant_trading_platform.models import MarketType, Venue, normalize_symbol


@dataclass(frozen=True)
class CryptoSpotPair:
    symbol: str
    base_asset: str
    quote_asset: str
    market_type: MarketType = MarketType.CRYPTO


@dataclass(frozen=True)
class SpotInstrumentRules:
    """Normalized public venue rules captured without credentials."""

    venue: Venue
    symbol: str
    base_asset: str
    quote_asset: str
    tick_size: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    min_notional: Decimal | None
    status: str
    timestamp_ms: int
    source: str

    def validate(self) -> None:
        pair = crypto_spot_pair(self.symbol)
        if self.base_asset != pair.base_asset or self.quote_asset != pair.quote_asset:
            raise ValueError("Instrument metadata identity mismatch")
        values = (self.tick_size, self.quantity_step, self.min_quantity)
        if any(not value.is_finite() or value <= 0 for value in values):
            raise ValueError("Instrument increments must be finite and positive")
        if self.min_notional is not None and (
            not self.min_notional.is_finite() or self.min_notional <= 0
        ):
            raise ValueError("Minimum notional must be finite and positive")
        if self.status != "trading" or self.timestamp_ms < 0 or not self.source:
            raise ValueError("Instrument is not available for spot paper simulation")


@dataclass(frozen=True)
class PaperSpotSizing:
    approved: bool
    reason_code: str
    quantity: Decimal
    buy_notional: Decimal
    sell_notional: Decimal
    common_quantity_step: Decimal


def _common_step(first: Decimal, second: Decimal) -> Decimal:
    first_exponent = first.as_tuple().exponent
    second_exponent = second.as_tuple().exponent
    if not isinstance(first_exponent, int) or not isinstance(second_exponent, int):
        raise ValueError("Quantity steps must be finite decimals")
    exponent = min(first_exponent, second_exponent)
    scale = Decimal(10) ** -exponent
    first_units = int(first * scale)
    second_units = int(second * scale)
    common_units = abs(first_units * second_units) // gcd(first_units, second_units)
    return Decimal(common_units) / scale


def size_paper_spread(
    requested_quote: Decimal,
    *,
    buy_price: Decimal,
    sell_price: Decimal,
    buy_available: Decimal,
    sell_available: Decimal,
    buy_rules: SpotInstrumentRules,
    sell_rules: SpotInstrumentRules,
) -> PaperSpotSizing:
    """Quantize a top-of-book paper spread to both venues' published rules."""
    buy_rules.validate()
    sell_rules.validate()
    if buy_rules.symbol != sell_rules.symbol:
        raise ValueError("Instrument rule symbols do not match")
    values = (requested_quote, buy_price, sell_price, buy_available, sell_available)
    if any(not value.is_finite() or value <= 0 for value in values):
        raise ValueError("Paper sizing values must be finite and positive")
    if buy_price % buy_rules.tick_size or sell_price % sell_rules.tick_size:
        return PaperSpotSizing(
            False, "instrument_price_step_mismatch", Decimal(0), Decimal(0), Decimal(0),
            _common_step(buy_rules.quantity_step, sell_rules.quantity_step),
        )
    step = _common_step(buy_rules.quantity_step, sell_rules.quantity_step)
    capacity = min(requested_quote / buy_price, buy_available, sell_available)
    quantity = (capacity / step).to_integral_value(rounding=ROUND_DOWN) * step
    buy_notional, sell_notional = quantity * buy_price, quantity * sell_price
    minimum_quantity = max(buy_rules.min_quantity, sell_rules.min_quantity)
    if quantity < minimum_quantity:
        return PaperSpotSizing(
            False,
            "instrument_min_quantity",
            quantity,
            buy_notional,
            sell_notional,
            step,
        )
    minimum_notional = max(
        buy_rules.min_notional or Decimal(0), sell_rules.min_notional or Decimal(0)
    )
    if min(buy_notional, sell_notional) < minimum_notional:
        return PaperSpotSizing(
            False,
            "instrument_min_notional",
            quantity,
            buy_notional,
            sell_notional,
            step,
        )
    return PaperSpotSizing(
        True,
        "instrument_rules_approved",
        quantity,
        buy_notional,
        sell_notional,
        step,
    )


CRYPTO_SPOT_PAIRS = (
    CryptoSpotPair("BTC/USDT", "BTC", "USDT"),
    CryptoSpotPair("ETH/USDT", "ETH", "USDT"),
    CryptoSpotPair("LTC/USDT", "LTC", "USDT"),
)
SUPPORTED_CRYPTO_SPOT_SYMBOLS = tuple(pair.symbol for pair in CRYPTO_SPOT_PAIRS)
SUPPORTED_CRYPTO_BASE_ASSETS = tuple(pair.base_asset for pair in CRYPTO_SPOT_PAIRS)


def crypto_spot_pair(symbol: str) -> CryptoSpotPair:
    """Return an allowlisted pair without guessing or converting the quote asset."""
    normalized = normalize_symbol(symbol)
    for pair in CRYPTO_SPOT_PAIRS:
        if pair.symbol == normalized:
            return pair
    raise ValueError("Unsupported paper crypto spot pair")
