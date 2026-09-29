"""Deterministic long-only spot signals for the local paper account."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class SignalSide(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class DirectionalSignal:
    side: SignalSide
    reason_code: str
    reference_price: Decimal
    momentum_pct: Decimal
    deviation_pct: Decimal
    expected_move_pct: Decimal
    expected_net_edge_pct: Decimal


def directional_signal(
    prices: tuple[Decimal, ...],
    *,
    fee_pct: Decimal,
    slippage_pct: Decimal,
    short_window: int = 12,
    long_window: int = 48,
    momentum_threshold_pct: Decimal = Decimal("0.12"),
    reversion_threshold_pct: Decimal = Decimal("0.35"),
) -> DirectionalSignal:
    """Return a fixed-parameter trend/reversion signal without future data.

    The reported edge is deliberately reduced by estimated round-trip costs.  It
    is a model estimate, not a promise of profit.
    """
    if short_window < 2 or long_window <= short_window:
        raise ValueError("Invalid strategy windows")
    if any(not value.is_finite() or value < 0 for value in (fee_pct, slippage_pct)):
        raise ValueError("Trading costs must be finite and non-negative")
    if any(not price.is_finite() or price <= 0 for price in prices):
        raise ValueError("Prices must be finite and positive")
    reference = prices[-1] if prices else Decimal(0)
    empty = DirectionalSignal(
        SignalSide.HOLD,
        "strategy_history_warmup",
        reference,
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
    )
    if len(prices) < long_window:
        return empty

    short_mean = sum(prices[-short_window:], Decimal(0)) / short_window
    long_mean = sum(prices[-long_window:], Decimal(0)) / long_window
    momentum = (short_mean / long_mean - 1) * 100
    deviation = (reference / long_mean - 1) * 100
    trend_vote = (
        1
        if momentum >= momentum_threshold_pct
        else -1
        if momentum <= -momentum_threshold_pct
        else 0
    )
    # Mean reversion is a fallback for a directionless regime.  Fighting an
    # established trend solely because price moved away from its mean creates
    # a mechanically contradictory signal.
    reversion_vote = 0
    if not trend_vote:
        reversion_vote = (
            1
            if deviation <= -reversion_threshold_pct
            else -1
            if deviation >= reversion_threshold_pct
            else 0
        )
    vote = trend_vote or reversion_vote
    expected_move = max(abs(momentum), abs(deviation))
    round_trip_cost = Decimal(2) * (fee_pct + slippage_pct)
    net_edge = expected_move - round_trip_cost
    if not vote:
        reason = "strategy_no_signal"
        side = SignalSide.HOLD
    elif net_edge <= 0:
        reason = "insufficient_edge_after_costs"
        side = SignalSide.HOLD
    else:
        reason = "directional_signal_approved"
        side = SignalSide.BUY if vote > 0 else SignalSide.SELL
    return DirectionalSignal(
        side,
        reason,
        reference,
        momentum,
        deviation,
        expected_move,
        max(Decimal(0), net_edge),
    )
