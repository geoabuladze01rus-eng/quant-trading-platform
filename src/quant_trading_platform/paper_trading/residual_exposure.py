"""Residual-exposure decisions for paper simulations.

This module is deliberately pure: it never submits an order.  It turns two
independently reported paper leg quantities into an explainable decision that a
future paper execution state machine may persist and act on.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class ResidualExposureDecision:
    status: str
    action: str
    hedge_side: str | None
    residual_quantity: Decimal
    residual_notional_usd: Decimal | None
    reason_code: str
    human_reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decimal(value: Decimal | str | int) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Expected a finite decimal") from error
    if not result.is_finite():
        raise ValueError("Expected a finite decimal")
    return result


def assess_residual_exposure(
    *,
    buy_quantity: Decimal | str | int,
    sell_quantity: Decimal | str | int,
    mark_price_usd: Decimal | str | int | None,
    max_unhedged_notional_usd: Decimal | str | int,
) -> ResidualExposureDecision:
    """Return a safe paper-only hedge decision for unequal leg outcomes.

    Positive residual is long base after a buy leg; negative residual is short.
    Missing or invalid marks halt automation rather than guessing an exposure.
    A future caller must persist the decision and re-check market data before
    simulating any hedge.
    """
    bought, sold = _decimal(buy_quantity), _decimal(sell_quantity)
    limit = _decimal(max_unhedged_notional_usd)
    if min(bought, sold, limit) < 0 or limit <= 0:
        raise ValueError("Quantities must be non-negative and limit positive")
    residual = bought - sold
    if residual == 0:
        return ResidualExposureDecision(
            "flat", "none", None, residual, Decimal(0),
            "paper_legs_matched", "Обе бумажные стороны исполнены в одинаковом объёме.",
        )
    if mark_price_usd is None:
        return ResidualExposureDecision(
            "halted", "halt", None, residual, None,
            "residual_mark_unavailable",
            "Остаточная позиция обнаружена, но её цена не подтверждена; симуляция остановлена.",
        )
    mark = _decimal(mark_price_usd)
    if mark <= 0:
        raise ValueError("Mark price must be positive")
    notional = abs(residual) * mark
    if notional > limit:
        return ResidualExposureDecision(
            "halted", "halt", None, residual, notional,
            "residual_exposure_limit_exceeded",
            "Остаточная позиция превышает лимит; автоматическое paper-хеджирование заблокировано.",
        )
    hedge_side = "sell" if residual > 0 else "buy"
    return ResidualExposureDecision(
        "hedge_required", "simulate_protective_hedge", hedge_side, residual, notional,
        "protective_paper_hedge_required",
        "Одна бумажная сторона исполнена не полностью; требуется защитный paper-хедж.",
    )
