"""Pure evidence composition. Reads cached observations without polling or execution."""

from dataclasses import asdict
from decimal import Decimal
from statistics import median

from quant_trading_platform.market_data.derivatives import (
    SIGNAL_SYMBOLS,
    DerivativesEvidenceService,
    MultiDerivativesEvidenceService,
)
from quant_trading_platform.market_data.liquidations import LiquidationWindow
from quant_trading_platform.market_data.service import MarketDataService, MultiMarketDataService
from quant_trading_platform.models import Venue, normalize_symbol


def get_signal_evidence(
    symbol: str,
    *,
    spot: MarketDataService | MultiMarketDataService | None,
    derivatives: DerivativesEvidenceService | MultiDerivativesEvidenceService | None,
    liquidations: LiquidationWindow | None,
    generated_at_ms: int,
    max_spot_age_ms: int = 1000,
    max_derivatives_age_ms: int = 360_000,
    venues: tuple[Venue, ...] = (Venue.BINANCE, Venue.BYBIT, Venue.OKX),
) -> dict[str, object]:
    symbol = normalize_symbol(symbol)
    if symbol not in SIGNAL_SYMBOLS:
        raise ValueError("Unsupported signal evidence symbol")
    if not venues or len(set(venues)) != len(venues) or any(
        venue not in (Venue.BINANCE, Venue.BYBIT, Venue.OKX) for venue in venues
    ):
        raise ValueError("Invalid evidence venues")
    spot_rows: list[dict[str, object]] = []
    derivative_rows: list[dict[str, object]] = []
    mids: dict[Venue, Decimal] = {}
    funding: dict[Venue, Decimal] = {}
    missing: list[str] = []
    stale: list[str] = []
    changes: list[dict[str, object]] = []
    spot_states = [] if spot is None else spot.snapshot()
    derivative_states = [] if derivatives is None else derivatives.snapshot()
    for venue in venues:
        book = None if spot is None else spot.book_for_simulation(venue, symbol)
        snapshot = None if derivatives is None else derivatives.fresh_snapshot(venue, symbol)
        for kind, value, rows, limit in (
            ("spot", book, spot_states, max_spot_age_ms),
            ("derivatives", snapshot, derivative_states, max_derivatives_age_ms),
        ):
            state = next(
                (r for r in rows if r.get("name") == venue.value and r.get("symbol") == symbol), {}
            )
            valid = value is not None and (
                value.venue == venue
                and value.symbol == symbol
                and 0 <= generated_at_ms - min(value.timestamp_ms, value.received_at_ms) <= limit
                and max(value.timestamp_ms, value.received_at_ms) <= generated_at_ms
                and state.get("status") == "ok"
            )
            if not valid:
                label = f"{kind}:{venue.value}"
                if state.get("status") == "stale" or (
                    value is not None
                    and generated_at_ms - min(value.timestamp_ms, value.received_at_ms) > limit
                ):
                    stale.append(label)
                else:
                    missing.append(label)
                if kind == "spot":
                    book = None
                else:
                    snapshot = None
        if book is not None:
            quote = book.to_quote()
            midpoint = (quote.bid + quote.ask) / Decimal(2)
            mids[venue] = midpoint
            bid_size = sum((level.quantity for level in book.bids), Decimal(0))
            ask_size = sum((level.quantity for level in book.asks), Decimal(0))
            spot_rows.append(
                {
                    "venue": venue.value,
                    "bid": str(quote.bid),
                    "ask": str(quote.ask),
                    "timestamp_ms": book.timestamp_ms,
                    "received_at_ms": book.received_at_ms,
                    "timestamp_source": book.timestamp_source,
                    "depth_imbalance": str((bid_size - ask_size) / (bid_size + ask_size)),
                    "contributing_venues": [venue.value],
                }
            )
        if snapshot is not None:
            row = asdict(snapshot)
            for key in ("mark_price", "index_price", "funding_rate", "open_interest"):
                row[key] = None if row[key] is None else str(row[key])
            mark, index = snapshot.mark_price, snapshot.index_price
            row["mark_spot_basis_pct"] = (
                None if mark is None or venue not in mids else str((mark / mids[venue] - 1) * 100)
            )
            row["index_mark_deviation_pct"] = (
                None if mark is None or index is None else str((mark / index - 1) * 100)
            )
            row["contributing_venues"] = [venue.value]
            derivative_rows.append(row)
            if snapshot.funding_rate is not None:
                funding[venue] = snapshot.funding_rate
            previous = None if derivatives is None else derivatives.previous_snapshot(venue, symbol)
            if (
                previous is not None
                and previous.open_interest is not None
                and (
                    snapshot.open_interest is not None
                    and previous.venue == snapshot.venue
                    and previous.instrument_id == snapshot.instrument_id
                    and previous.open_interest_unit == snapshot.open_interest_unit
                    and previous.timestamp_ms < snapshot.timestamp_ms
                )
            ):
                changes.append(
                    {
                        "venue": venue.value,
                        "unit": snapshot.open_interest_unit,
                        "change": str(snapshot.open_interest - previous.open_interest),
                        "change_pct": str(
                            (snapshot.open_interest / previous.open_interest - 1) * 100
                        ),
                        "contributing_venues": [venue.value],
                    }
                )
    consensus = (
        None
        if not funding
        else {
            "median": str(median(funding.values())),
            "min": str(min(funding.values())),
            "max": str(max(funding.values())),
            "contributing_venues": [v.value for v in funding],
            "warning": "Funding intervals may differ; raw rates are contextual only",
        }
    )
    required_per_class = 1 if len(venues) == 1 else 2
    usable = len(spot_rows) >= required_per_class and len(derivative_rows) >= required_per_class
    # A complete OKX-only feed is healthy for its configured scope. This does NOT
    # imply independent cross-venue corroboration (see the explicit warning below).
    # Missing/stale required data still fail closed.
    quality = "insufficient" if not usable else (
        "degraded" if missing or stale else "healthy"
    )
    spread = (
        None
        if len(mids) < 2
        else {
            "midpoint_range_pct": str((max(mids.values()) / min(mids.values()) - 1) * 100),
            "contributing_venues": [v.value for v in mids],
        }
    )
    return {
        "symbol": symbol,
        "generated_at_ms": generated_at_ms,
        "spot": {"venues": spot_rows, "cross_venue_spread": spread},
        "derivatives": {
            "venues": derivative_rows,
            "funding_consensus": consensus,
            "open_interest_change": changes or None,
            "mark_spot_basis": [
                {
                    "venue": r["venue"],
                    "basis_pct": r["mark_spot_basis_pct"],
                    "contributing_venues": r["contributing_venues"],
                }
                for r in derivative_rows
            ],
        },
        "liquidations": (liquidations or LiquidationWindow()).summary(symbol, generated_at_ms),
        "quality": {
            "status": quality,
            "fresh_sources": len(spot_rows) + len(derivative_rows),
            "expected_sources": 2 * len(venues),
            "missing": missing,
            "stale": stale,
            "warnings": [
                "Liquidation streams are partial observations",
                "Native open-interest units are not cross-venue comparable",
                "Missing data cannot be counted as confirmation",
                *(["Single OKX venue: no independent cross-venue corroboration"]
                  if len(venues) == 1 else []),
            ],
        },
        "execution": {"paper_only": True, "live_execution": False},
    }
