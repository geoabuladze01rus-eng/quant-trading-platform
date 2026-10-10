"""Position sizing from CRYPTO SPOT SIGNAL WATCH v6.0 (section 9).

Risk per trade is 0.5 % of available trading capital by default, never above 1 %.
Size is the risk budget divided by the effective risk per coin, after fees and
slippage at the worst-case buy price (the top of the entry zone). The result is
capped by free USDT. Pure arithmetic: nothing is ordered or reserved here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from quant_trading_platform.strategies.spot_signal_rules import (
    DEFAULT_RISK_PCT,
    MAX_RISK_PCT,
    TradePlan,
)

HUNDRED: Final[Decimal] = Decimal(100)

SizingLimit = Literal["risk", "cash"]


@dataclass(frozen=True, slots=True)
class PositionSize:
    coins: Decimal
    notional_usdt: Decimal
    risk_usdt: Decimal
    limited_by: SizingLimit | None
    reason: str | None  # set when the position cannot be opened


def size_position(
    plan: TradePlan,
    *,
    trading_capital_usdt: Decimal,
    free_usdt: Decimal,
    risk_pct: Decimal = DEFAULT_RISK_PCT,
    min_order_usdt: Decimal = Decimal(10),
) -> PositionSize:
    def rejected(reason: str) -> PositionSize:
        return PositionSize(Decimal(0), Decimal(0), Decimal(0), None, reason)

    if not (Decimal(0) < risk_pct <= MAX_RISK_PCT):
        return rejected("риск на сделку должен быть больше 0 и не выше 1%")
    if trading_capital_usdt <= 0 or free_usdt < 0:
        return rejected("капитал и свободный USDT должны быть неотрицательными")
    if not (plan.stop_loss < plan.entry_low <= plan.entry_high):
        return rejected("стоп-лосс должен быть ниже зоны входа")

    cost = plan.fee_rate + plan.slippage_rate
    effective_entry = plan.entry_high * (1 + cost)
    effective_stop = plan.stop_loss * (1 - cost)
    risk_per_coin = effective_entry - effective_stop
    if risk_per_coin <= 0:
        return rejected("эффективный риск на монету не положительный")

    risk_budget = trading_capital_usdt * risk_pct / HUNDRED
    coins_by_risk = risk_budget / risk_per_coin
    cash_cap = free_usdt / effective_entry
    coins = min(coins_by_risk, cash_cap)
    limited_by: SizingLimit = "risk" if coins_by_risk <= cash_cap else "cash"
    notional = coins * effective_entry

    if notional < min_order_usdt:
        return rejected(
            f"размер ордера {notional:.2f} USDT ниже минимума {min_order_usdt} USDT"
        )
    return PositionSize(
        coins=coins,
        notional_usdt=notional,
        risk_usdt=coins * risk_per_coin,
        limited_by=limited_by,
        reason=None,
    )
