"""Pure, review-only residual decisions over immutable simulated leg events."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class PaperLegEvent:
    execution_group_id: str
    event_id: str
    side: str
    venue: str
    simulated_order_id: str
    filled_quantity: Decimal
    timestamp_ms: int

    def __post_init__(self) -> None:
        if not self.execution_group_id.strip() or not self.event_id.strip():
            raise ValueError("Stable group and event IDs are required")
        if self.side not in ("buy", "sell") or not self.venue.strip():
            raise ValueError("Known side and venue are required")
        if not self.simulated_order_id.strip():
            raise ValueError("A simulated order ID is required")
        if not isinstance(self.filled_quantity, Decimal) or (
            not self.filled_quantity.is_finite() or self.filled_quantity <= 0
        ):
            raise ValueError("Fill quantity must be a positive finite Decimal")
        if type(self.timestamp_ms) is not int or self.timestamp_ms <= 0:
            raise ValueError("Fill timestamp must be positive milliseconds")


@dataclass(frozen=True)
class ResidualExposureDecision:
    status: str
    action: str
    hedge_side: str | None
    residual_quantity: Decimal
    residual_notional_usd: Decimal | None
    reason_code: str
    human_reason: str
    buy_quantity: Decimal
    sell_quantity: Decimal
    filled_leg: str
    mark_price_usd: Decimal | None
    mark_source: str | None
    mark_timestamp_ms: int | None
    mark_age_ms: int | None
    configured_cap_usd: Decimal
    source_event_ids: tuple[str, ...]
    reason_not_executed: str
    paper_only: bool = True
    live_execution: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["hedge_proposal"] = (
            {
                "side": self.hedge_side,
                "quantity": abs(self.residual_quantity),
                "mark_price_usd": self.mark_price_usd,
                "mark_source": self.mark_source,
                "mark_age_ms": self.mark_age_ms,
                "residual_notional_usd": self.residual_notional_usd,
                "configured_cap_usd": self.configured_cap_usd,
                "confirmable": False,
                "execution_enabled": False,
            }
            if self.status == "hedge_required" else None
        )
        return payload

    def audit_payload(
        self, *, execution_group_id: str, strategy: str, timestamp_ms: int
    ) -> dict[str, Any]:
        return {
            "execution_group_id": execution_group_id,
            "event_type": "residual_decision",
            "market_scope": "crypto",
            "strategy": strategy,
            "timestamp_ms": timestamp_ms,
            "status": self.status,
            "reason_code": self.reason_code,
            "human_reason": self.human_reason,
            "buy_quantity": self.buy_quantity,
            "sell_quantity": self.sell_quantity,
            "residual_quantity": self.residual_quantity,
            "residual_notional_usd": self.residual_notional_usd,
            "mark_source": self.mark_source,
            "mark_timestamp_ms": self.mark_timestamp_ms,
            "mark_age_ms": self.mark_age_ms,
            "configured_cap_usd": self.configured_cap_usd,
            "source_event_ids": self.source_event_ids,
            "paper_only": True,
            "live_execution": False,
        }


def _decimal(value: Decimal | str | int) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValueError("Float and Boolean are not financial Decimal inputs")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Expected a finite decimal") from error
    if not result.is_finite():
        raise ValueError("Expected a finite decimal")
    return result


def assess_residual_exposure(
    *, buy_quantity: Decimal | str | int, sell_quantity: Decimal | str | int,
    mark_price_usd: Decimal | str | int | None,
    max_unhedged_notional_usd: Decimal | str | int,
    mark_source: str | None = None, mark_timestamp_ms: int | None = None,
    now_ms: int | None = None, max_mark_age_ms: int = 1_000,
    reconciliation_ok: bool = True, source_event_ids: tuple[str, ...] = (),
) -> ResidualExposureDecision:
    """Assess paper leg imbalance without authorizing or executing a hedge."""
    bought, sold = _decimal(buy_quantity), _decimal(sell_quantity)
    limit = _decimal(max_unhedged_notional_usd)
    if min(bought, sold, limit) < 0 or limit <= 0:
        raise ValueError("Quantities must be non-negative and cap positive")
    if type(reconciliation_ok) is not bool:
        raise ValueError("reconciliation_ok must be Boolean")
    if type(max_mark_age_ms) is not int or max_mark_age_ms <= 0:
        raise ValueError("max_mark_age_ms must be positive")
    residual = bought - sold
    leg = (
        "matched" if residual == 0 else "buy_only" if sold == 0 else
        "sell_only" if bought == 0 else "buy_more" if residual > 0 else "sell_more"
    )
    mark: Decimal | None = None
    notional: Decimal | None = None
    if mark_price_usd is not None:
        mark = _decimal(mark_price_usd)
        if mark <= 0:
            raise ValueError("Mark price must be positive")
        notional = abs(residual) * mark
    age = (
        now_ms - mark_timestamp_ms
        if type(now_ms) is int and type(mark_timestamp_ms) is int else None
    )

    def decide(
        status: str, action: str, side: str | None, code: str, reason: str
    ) -> ResidualExposureDecision:
        return ResidualExposureDecision(
            status, action, side, residual, Decimal(0) if residual == 0 else notional,
            code, reason, bought, sold, leg, mark, mark_source, mark_timestamp_ms,
            age, limit, source_event_ids,
            "Automatic paper hedge execution is disabled; operator review only.",
        )

    if not reconciliation_ok:
        return decide(
            "halted", "halt", None, "residual_accounting_mismatch",
            "Учёт не сходится; предложение paper-хеджа заблокировано до проверки.",
        )
    if residual == 0:
        return decide(
            "flat", "none", None, "paper_legs_matched",
            "Обе бумажные стороны исполнены в одинаковом объёме.",
        )
    if mark is None or not mark_source or mark_timestamp_ms is None or now_ms is None:
        return decide(
            "halted", "halt", None, "residual_mark_unavailable",
            "Остаточная позиция обнаружена, но источник и время цены не подтверждены.",
        )
    if age is None or age < 0 or age > max_mark_age_ms:
        return decide(
            "halted", "halt", None, "residual_mark_stale",
            "Цена остаточной позиции устарела или датирована будущим; симуляция остановлена.",
        )
    assert notional is not None
    if notional > limit:
        return decide(
            "halted", "halt", None, "residual_exposure_limit_exceeded",
            "Остаточная позиция превышает лимит; paper-хедж не предлагается.",
        )
    return decide(
        "hedge_required", "review_paper_hedge_proposal",
        "sell" if residual > 0 else "buy", "protective_paper_hedge_required",
        "Исполнения бумажных сторон различаются; требуется ручная проверка предложения.",
    )


def assess_leg_events(
    events: Sequence[PaperLegEvent], *, mark_price_usd: Decimal | str | int | None,
    max_unhedged_notional_usd: Decimal | str | int,
    mark_source: str | None = None, mark_timestamp_ms: int | None = None,
    now_ms: int | None = None, max_mark_age_ms: int = 1_000,
    reconciliation_ok: bool = True,
) -> ResidualExposureDecision:
    """Replay stable event IDs exactly once; reject conflicting replays."""
    if len({event.execution_group_id for event in events}) > 1:
        raise ValueError("Leg events must belong to one execution group")
    seen: dict[str, PaperLegEvent] = {}
    for event in events:
        previous = seen.setdefault(event.event_id, event)
        if previous != event:
            raise ValueError("Duplicate event ID has conflicting contents")
    bought = sum(
        (item.filled_quantity for item in seen.values() if item.side == "buy"), Decimal(0)
    )
    sold = sum(
        (item.filled_quantity for item in seen.values() if item.side == "sell"), Decimal(0)
    )
    return assess_residual_exposure(
        buy_quantity=bought, sell_quantity=sold, mark_price_usd=mark_price_usd,
        max_unhedged_notional_usd=max_unhedged_notional_usd,
        mark_source=mark_source, mark_timestamp_ms=mark_timestamp_ms, now_ms=now_ms,
        max_mark_age_ms=max_mark_age_ms, reconciliation_ok=reconciliation_ok,
        source_event_ids=tuple(sorted(seen)),
    )


__all__ = [
    "PaperLegEvent", "ResidualExposureDecision", "assess_leg_events",
    "assess_residual_exposure",
]
