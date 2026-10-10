"""Builds spot signal candidates from completed OKX candles using the v6.0 rules.

Pure computation: candles are passed in, nothing is fetched, sent or ordered.
Every candidate is evaluated by spot_signal_rules.evaluate, so the generator
cannot make a deliverable signal that the rules would reject.

Definitions chosen here (to be confirmed by the owner, see the PR notes):
- Entry zone: from the 1H breakout level (highest high of the previous 20 1H
  candles) up to 0.2% above it. Price already above the zone is rejected.
- Stop: 2 x ATR(14, 1H) below the zone's lower bound.
- Targets: the three nearest 4H swing highs above the zone (pivot high with
  3 confirming candles on each side). Targets must sit in market structure.
- Liquidity: a proxy, average 1H quote volume over 20 candles. The order book
  is not fetched, so this is not a true liquidity measurement.
- News: not checked by this module. The caller must pass news_checked, and it
  is False until a news source is connected, so nothing is deliverable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from quant_trading_platform.strategies import spot_signal_rules as rules
from quant_trading_platform.strategies.indicators import atr, ema, rsi, vwap
from quant_trading_platform.strategies.spot_momentum import (
    BAR_MS,
    DailyCandle,
    validate_candles,
)

PIVOT_SPAN: Final[int] = 3
BREAKOUT_LOOKBACK: Final[int] = 20
VWAP_LOOKBACK: Final[int] = 24
VOLUME_LOOKBACK: Final[int] = 20
ENTRY_BAND: Final[Decimal] = Decimal("0.002")
STOP_ATR_MULTIPLE: Final[Decimal] = Decimal("2")
STRONG_LEVEL_BAND: Final[Decimal] = Decimal("0.002")
# Proxy liquidity floor: average 1H quote volume in USDT. Owner must confirm.
MIN_AVG_QUOTE_VOLUME_USDT: Final[Decimal] = Decimal("1000000")
FEE_RATE: Final[Decimal] = Decimal("0.001")
SLIPPAGE_RATE: Final[Decimal] = Decimal("0.0005")
ZERO: Final[Decimal] = Decimal(0)


@dataclass(frozen=True, slots=True)
class CandidateResult:
    symbol: str
    evaluation: rules.Evaluation
    plan: rules.TradePlan | None
    current_price: Decimal | None


def swing_highs(candles: Sequence[DailyCandle], span: int = PIVOT_SPAN) -> list[Decimal]:
    """Pivot highs confirmed by `span` lower highs on each side (last span candles excluded)."""
    found: list[Decimal] = []
    for index in range(span, len(candles) - span):
        high = candles[index].high
        left = all(high > candles[j].high for j in range(index - span, index))
        right = all(high > candles[j].high for j in range(index + 1, index + span + 1))
        if left and right:
            found.append(high)
    return found


def resistances_above(candles_4h: Sequence[DailyCandle], level: Decimal) -> list[Decimal]:
    return sorted({high for high in swing_highs(candles_4h) if high > level})


def _last(series: list[Decimal | None]) -> Decimal:
    value = series[-1]
    if value is None:
        raise ValueError("indicator is not defined for the latest candle")
    return value


def _validate(
    candles: tuple[DailyCandle, ...], bar: str, now_ms: int
) -> None:
    validate_candles(candles, now_ms=now_ms, interval_ms=BAR_MS[bar])


def build_candidate(
    symbol: str,
    *,
    candles_1d: tuple[DailyCandle, ...],
    candles_4h: tuple[DailyCandle, ...],
    candles_1h: tuple[DailyCandle, ...],
    candles_15m: tuple[DailyCandle, ...],
    now_ms: int,
    news_checked: bool,
) -> CandidateResult:
    def rejected(reason: str, price: Decimal | None = None) -> CandidateResult:
        evaluation = rules.Evaluation("NONE", False, None, (reason,))
        return CandidateResult(symbol, evaluation, None, price)

    try:
        _validate(candles_1d, "1Dutc", now_ms)
        _validate(candles_4h, "4H", now_ms)
        _validate(candles_1h, "1H", now_ms)
        _validate(candles_15m, "15m", now_ms)
    except ValueError as error:
        return rejected(f"данные не подтверждены: {error}")

    closes_1d = [c.close for c in candles_1d]
    closes_4h = [c.close for c in candles_4h]
    closes_1h = [c.close for c in candles_1h]
    closes_15m = [c.close for c in candles_15m]
    highs_1h = [c.high for c in candles_1h]
    lows_1h = [c.low for c in candles_1h]
    volumes_1h = [c.volume for c in candles_1h]
    price = closes_1h[-1]
    if sum(volumes_1h[-VWAP_LOOKBACK:], ZERO) <= ZERO:
        return rejected("нулевой объём за окно VWAP: данные не пригодны", price)

    ema20_4h = _last(ema(closes_4h, 20))
    ema50_4h = _last(ema(closes_4h, 50))
    trend_4h_up = closes_4h[-1] > ema50_4h and ema20_4h > ema50_4h

    ema20_1d = _last(ema(closes_1d, 20))
    ema50_1d = _last(ema(closes_1d, 50))
    trend_1d_up = closes_1d[-1] > ema50_1d
    trend_1d_not_strong_down = not (closes_1d[-1] < ema50_1d and ema20_1d < ema50_1d)

    ema20_15m = _last(ema(closes_15m, 20))
    previous_high = max(highs_1h[-BREAKOUT_LOOKBACK - 1:-1])
    breakout = price > previous_high

    atr_1h = _last(atr(highs_1h, lows_1h, closes_1h, 14))
    rsi_1h = _last(rsi(closes_1h, 14))
    vwap_1h = vwap(
        highs_1h[-VWAP_LOOKBACK:], lows_1h[-VWAP_LOOKBACK:],
        closes_1h[-VWAP_LOOKBACK:], volumes_1h[-VWAP_LOOKBACK:],
    )
    average_volume = sum(volumes_1h[-VOLUME_LOOKBACK - 1:-1], ZERO) / VOLUME_LOOKBACK
    volume_confirmed = volumes_1h[-1] > average_volume
    average_quote = sum(
        (c.close * c.volume for c in candles_1h[-VOLUME_LOOKBACK:]), ZERO
    ) / VOLUME_LOOKBACK
    liquidity_ok = average_quote >= MIN_AVG_QUOTE_VOLUME_USDT

    entry_low = previous_high
    entry_high = previous_high * (1 + ENTRY_BAND)
    if price > entry_high:
        return rejected("цена выше зоны входа: не догонять импульс", price)

    targets_all = resistances_above(candles_4h, entry_high)
    if len(targets_all) < 3:
        return rejected(
            "нет трёх уровней сопротивления на 4H выше зоны входа", price
        )
    targets = (targets_all[0], targets_all[1], targets_all[2])
    stop_loss = entry_low - STOP_ATR_MULTIPLE * atr_1h

    strong_level = any(
        abs(high - previous_high) <= previous_high * STRONG_LEVEL_BAND
        for high in swing_highs(candles_4h)
    )
    confirmations: set[str] = set()
    if trend_4h_up:
        confirmations.add("trend_4h_1h")
    if breakout:
        confirmations.add("structure")
    if strong_level:
        confirmations.add("strong_level")
    if volume_confirmed:
        confirmations.add("volume")
    if rsi_1h > Decimal(50):
        confirmations.add("momentum")
    if price > vwap_1h:
        confirmations.add("ema_vwap")
    if liquidity_ok:
        confirmations.add("liquidity")

    plan = rules.TradePlan(
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        targets=targets,
        fee_rate=FEE_RATE,
        slippage_rate=SLIPPAGE_RATE,
    )
    context = rules.MarketContext(
        trend_4h_up=trend_4h_up,
        trend_1d_not_strong_down=trend_1d_not_strong_down,
        structure_1h_confirmed=breakout,
        data_fresh=True,
        sources_verified=True,
        news_risk_critical=False,
        liquidity_ok=liquidity_ok,
        volume_confirmed=volume_confirmed,
        multi_timeframe_aligned=trend_1d_up and trend_4h_up and breakout
        and closes_15m[-1] > ema20_15m,
        confirmations=frozenset(confirmations),
        news_checked=news_checked,
    )
    evaluation = rules.evaluate(plan, context, current_price=price)
    return CandidateResult(symbol, evaluation, plan, price)
