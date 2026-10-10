"""Deliverability rules from CRYPTO SPOT SIGNAL WATCH v6.0 (sections 7, 8, 18).

Pure functions over explicit inputs: no network, no orders. A candidate is
deliverable only when it has HIGH or VERY HIGH confidence, net risk/reward of at
least 1:2 after fees and slippage, and passes every hard gate. Anything weaker is
reported with reasons and is never sent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

Grade = Literal["NONE", "MEDIUM", "HIGH", "VERY_HIGH"]

# Section 7 lists ten categories. Two indicators from one category count once.
CONFIRMATION_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "trend_4h_1h",
        "strong_level",
        "volume",
        "structure",
        "retest_hold",
        "ema_vwap",
        "momentum",
        "liquidity",
        "no_near_resistance",
        "risk_reward",
    }
)
MIN_CONFIRMATIONS: Final[int] = 4
VERY_HIGH_CONFIRMATIONS: Final[int] = 7
MIN_RISK_REWARD: Final[Decimal] = Decimal("2")
MAX_RISK_PCT: Final[Decimal] = Decimal("1")
DEFAULT_RISK_PCT: Final[Decimal] = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class TradePlan:
    entry_low: Decimal
    entry_high: Decimal
    stop_loss: Decimal
    targets: tuple[Decimal, Decimal, Decimal]
    fee_rate: Decimal  # per side, e.g. Decimal("0.001") for 0.1%
    slippage_rate: Decimal  # per side


@dataclass(frozen=True, slots=True)
class MarketContext:
    trend_4h_up: bool
    trend_1d_not_strong_down: bool
    structure_1h_confirmed: bool
    data_fresh: bool
    sources_verified: bool
    news_risk_critical: bool
    liquidity_ok: bool
    volume_confirmed: bool
    multi_timeframe_aligned: bool  # 1D, 4H, 1H and 15M agree
    confirmations: frozenset[str]
    news_checked: bool = True  # False when no news source was consulted


@dataclass(frozen=True, slots=True)
class Evaluation:
    grade: Grade
    deliverable: bool
    net_risk_reward: Decimal | None
    reasons: tuple[str, ...]


def net_risk_reward(plan: TradePlan) -> Decimal:
    """Risk/reward after fees and slippage, using the worst-case buy price (entry_high)."""
    cost = plan.fee_rate + plan.slippage_rate
    effective_entry = plan.entry_high * (1 + cost)
    effective_stop = plan.stop_loss * (1 - cost)
    effective_target = plan.targets[0] * (1 - cost)
    risk = effective_entry - effective_stop
    if risk <= 0:
        raise ValueError("effective risk must be positive")
    return (effective_target - effective_entry) / risk


def _plan_problems(plan: TradePlan) -> list[str]:
    problems: list[str] = []
    if not (plan.stop_loss < plan.entry_low <= plan.entry_high):
        problems.append("стоп-лосс должен быть ниже зоны входа")
    if not (plan.targets[0] < plan.targets[1] < plan.targets[2]):
        problems.append("цели должны возрастать: TP1 < TP2 < TP3")
    if plan.targets[0] <= plan.entry_high:
        problems.append("TP1 должен быть выше зоны входа")
    if plan.fee_rate < 0 or plan.slippage_rate < 0:
        problems.append("комиссия и проскальзывание не могут быть отрицательными")
    return problems


def evaluate(
    plan: TradePlan,
    context: MarketContext,
    *,
    current_price: Decimal,
) -> Evaluation:
    reasons: list[str] = []

    reasons.extend(_plan_problems(plan))
    if not context.data_fresh:
        reasons.append("данные устарели или не подтверждены")
    if not context.sources_verified:
        reasons.append("источник данных не подтверждён")
    if not context.news_checked:
        reasons.append("новостной фильтр не проверен: сигнал не выдаётся")
    if context.news_risk_critical:
        reasons.append("критический новостной риск: новые сделки запрещены")
    if not context.trend_4h_up:
        reasons.append("тренд 4H не восходящий: покупка против тренда запрещена")
    if not context.trend_1d_not_strong_down:
        reasons.append("сильный нисходящий тренд 1D")
    if not context.structure_1h_confirmed:
        reasons.append("структура 1H не подтверждена")
    if current_price > plan.entry_high:
        reasons.append("цена выше зоны входа: не покупать по завышенной цене")

    unknown = context.confirmations - CONFIRMATION_CATEGORIES
    if unknown:
        raise ValueError(f"unknown confirmation categories: {sorted(unknown)}")
    count = len(context.confirmations)
    if count < MIN_CONFIRMATIONS:
        reasons.append(f"подтверждений {count}, нужно минимум {MIN_CONFIRMATIONS}")

    rr: Decimal | None = None
    if not _plan_problems(plan):
        rr = net_risk_reward(plan)
        if rr < MIN_RISK_REWARD:
            reasons.append(f"R/R после издержек {rr:.2f} ниже минимума 1:2")

    if reasons:
        return Evaluation("NONE", False, rr, tuple(reasons))

    if (
        count >= VERY_HIGH_CONFIRMATIONS
        and context.multi_timeframe_aligned
        and context.volume_confirmed
        and context.liquidity_ok
    ):
        return Evaluation("VERY_HIGH", True, rr, ())
    if context.liquidity_ok and context.volume_confirmed:
        return Evaluation("HIGH", True, rr, ())
    return Evaluation(
        "MEDIUM",
        False,
        rr,
        ("уверенность MEDIUM: сигналы MEDIUM и LOW не отправляются",),
    )


def sort_by_quality(evaluations: Sequence[Evaluation]) -> list[Evaluation]:
    order = {"VERY_HIGH": 3, "HIGH": 2, "MEDIUM": 1, "NONE": 0}
    return sorted(evaluations, key=lambda e: order[e.grade], reverse=True)
