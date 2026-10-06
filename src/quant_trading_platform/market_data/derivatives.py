"""Validated derivatives snapshots for read-only signal evidence."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TypeAlias

from quant_trading_platform.market_data.models import StaleMarketDataError
from quant_trading_platform.models import Venue, normalize_symbol

DecimalInput: TypeAlias = Decimal | str | int
_SIGNAL_SYMBOLS = frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"})


@dataclass(frozen=True)
class DerivativesSnapshot:
    venue: Venue
    symbol: str
    instrument_id: str
    timestamp_ms: int
    received_at_ms: int
    mark_price: Decimal | None
    index_price: Decimal | None
    funding_rate: Decimal | None
    next_funding_time_ms: int | None
    open_interest: Decimal | None
    open_interest_unit: str | None
    source_fields: tuple[str, ...]


def derivatives_instrument_id(venue: Venue, symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if normalized not in _SIGNAL_SYMBOLS:
        raise ValueError("Unsupported derivatives signal symbol")
    if venue in (Venue.BINANCE, Venue.BYBIT):
        return normalized.replace("/", "")
    if venue == Venue.OKX:
        return f"{normalized.replace('/', '-')}-SWAP"
    raise ValueError("Unsupported derivatives venue")


def _decimal(
    value: DecimalInput | None,
    *,
    name: str,
    positive: bool,
) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{name} must be finite") from error
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    if positive and parsed <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return parsed


def normalize_derivatives_snapshot(
    *,
    venue: Venue,
    symbol: str,
    instrument_id: str,
    timestamp_ms: int,
    received_at_ms: int,
    mark_price: DecimalInput | None,
    index_price: DecimalInput | None,
    funding_rate: DecimalInput | None,
    next_funding_time_ms: int | None,
    open_interest: DecimalInput | None,
    open_interest_unit: str | None,
    source_fields: tuple[str, ...],
    max_age_ms: int,
) -> DerivativesSnapshot:
    normalized_symbol = normalize_symbol(symbol)
    expected_instrument = derivatives_instrument_id(venue, normalized_symbol)
    if instrument_id != expected_instrument:
        raise ValueError("Derivatives instrument identity mismatch")
    if any(type(value) is not int or value < 0 for value in (timestamp_ms, received_at_ms)):
        raise ValueError("Invalid derivatives timestamp")
    if type(max_age_ms) is not int or max_age_ms <= 0:
        raise ValueError("max_age_ms must be positive")
    if timestamp_ms > received_at_ms:
        raise ValueError("Future derivatives timestamp")
    if received_at_ms - timestamp_ms > max_age_ms:
        raise StaleMarketDataError("Stale derivatives data")
    if next_funding_time_ms is not None and (
        type(next_funding_time_ms) is not int or next_funding_time_ms < 0
    ):
        raise ValueError("Invalid next funding timestamp")

    mark = _decimal(mark_price, name="mark_price", positive=True)
    index = _decimal(index_price, name="index_price", positive=True)
    funding = _decimal(funding_rate, name="funding_rate", positive=False)
    interest = _decimal(open_interest, name="open_interest", positive=True)
    unit = None if open_interest_unit is None else open_interest_unit.strip()
    if interest is not None and not unit:
        raise ValueError("Open interest unit is required")
    if interest is None and unit:
        raise ValueError("Open interest unit requires open interest")

    return DerivativesSnapshot(
        venue=venue,
        symbol=normalized_symbol,
        instrument_id=instrument_id,
        timestamp_ms=timestamp_ms,
        received_at_ms=received_at_ms,
        mark_price=mark,
        index_price=index,
        funding_rate=funding,
        next_funding_time_ms=next_funding_time_ms,
        open_interest=interest,
        open_interest_unit=unit,
        source_fields=tuple(source_fields),
    )
