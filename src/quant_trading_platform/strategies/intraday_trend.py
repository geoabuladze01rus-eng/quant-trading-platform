"""Read-only intraday research: 4H trend context and 1H entry review.

Outputs are review candidates with a reference stop. They never create a paper or live
order, and no take-profit is computed here, so nothing is a complete trade signal.
Inputs must be completed candles in ascending order (see validate_candles).
"""

from dataclasses import dataclass
from decimal import Decimal

from quant_trading_platform.strategies.indicators import atr, ema, rsi, vwap
from quant_trading_platform.strategies.spot_momentum import DailyCandle

ZERO = Decimal(0)
BREAKOUT_LOOKBACK = 20
VWAP_LOOKBACK = 24
VOLUME_LOOKBACK = 20
STOP_ATR_MULTIPLE = Decimal("2")
MIN_1H_CANDLES = 101
MIN_4H_CANDLES = 60


@dataclass(frozen=True)
class IntradayCandidate:
    symbol: str
    action: str  # "entry_review" | "no_new_entry" | "exit_review"
    reason_code: str
    human_reason: str
    confirmations: tuple[str, ...]
    close: Decimal
    stop_reference: Decimal | None


def _last(series: list[Decimal | None]) -> Decimal:
    value = series[-1]
    if value is None:
        raise ValueError("Indicator is not yet defined for the latest candle")
    return value


def intraday_candidate(
    symbol: str,
    candles_4h: tuple[DailyCandle, ...],
    candles_1h: tuple[DailyCandle, ...],
) -> IntradayCandidate:
    if len(candles_4h) < MIN_4H_CANDLES or len(candles_1h) < MIN_1H_CANDLES:
        raise ValueError("Not enough completed candles for intraday research")
    closes_4h = [c.close for c in candles_4h]
    ema20_4h = _last(ema(closes_4h, 20))
    ema50_4h = _last(ema(closes_4h, 50))
    close_4h = candles_4h[-1].close

    closes_1h = [c.close for c in candles_1h]
    highs_1h = [c.high for c in candles_1h]
    lows_1h = [c.low for c in candles_1h]
    volumes_1h = [c.volume for c in candles_1h]
    close = candles_1h[-1].close
    atr_1h = _last(atr(highs_1h, lows_1h, closes_1h, 14))
    stop_reference = close - STOP_ATR_MULTIPLE * atr_1h

    if close_4h < ema50_4h:
        return IntradayCandidate(
            symbol, "exit_review", "trend_4h_down",
            "4H цена ниже EMA 50: тренд ослаб, новых входов нет.",
            (), close, None,
        )
    if not (close_4h > ema50_4h and ema20_4h > ema50_4h):
        return IntradayCandidate(
            symbol, "no_new_entry", "trend_4h_unconfirmed",
            "4H тренд не подтверждён: нужны цена выше EMA 50 и EMA 20 выше EMA 50.",
            (), close, None,
        )

    confirmations = ["4H восходящий тренд (EMA 20 > EMA 50, цена выше EMA 50)"]
    previous_high = max(highs_1h[-BREAKOUT_LOOKBACK - 1:-1])
    if close > previous_high:
        confirmations.append("1H закрытие выше 20-свечного максимума")
    rsi_1h = _last(rsi(closes_1h, 14))
    if rsi_1h > Decimal(50):
        confirmations.append("1H RSI 14 выше 50")
    vwap_1h = vwap(
        highs_1h[-VWAP_LOOKBACK:], lows_1h[-VWAP_LOOKBACK:],
        closes_1h[-VWAP_LOOKBACK:], volumes_1h[-VWAP_LOOKBACK:],
    )
    if close > vwap_1h:
        confirmations.append("1H цена выше VWAP 24 свечей")
    average_volume = sum(volumes_1h[-VOLUME_LOOKBACK - 1:-1], ZERO) / VOLUME_LOOKBACK
    if volumes_1h[-1] > average_volume:
        confirmations.append("1H объём выше среднего за 20 свечей")

    breakout = "1H закрытие выше 20-свечного максимума" in confirmations
    # Require the 4H trend, the 1H breakout and at least two supporting checks.
    if breakout and len(confirmations) >= 5:
        return IntradayCandidate(
            symbol, "entry_review", "intraday_breakout_confirmed",
            "Подтверждённый 1H пробой в 4H тренде: проверьте вход и стоп.",
            tuple(confirmations), close, stop_reference,
        )
    if breakout and len(confirmations) >= 4:
        return IntradayCandidate(
            symbol, "entry_review", "intraday_breakout_partial",
            "1H пробой в 4H тренде с частичным подтверждением: только для ручной проверки.",
            tuple(confirmations), close, stop_reference,
        )
    return IntradayCandidate(
        symbol, "no_new_entry", "intraday_unconfirmed",
        "Нового 1H входа нет: пробой или подтверждения не выполнены.",
        tuple(confirmations), close, None,
    )
