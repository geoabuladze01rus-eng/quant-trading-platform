from decimal import Decimal

from quant_trading_platform.strategies.spot_momentum import BAR_MS, DailyCandle
from quant_trading_platform.strategies.spot_signal_generator import (
    CandidateResult,
    build_candidate,
)

# Fixed reference time; every bar's latest candle sits one to two intervals before it.
NOW_MS = 1_700_000_000_000


def candles(
    bar: str,
    closes: list[Decimal],
    *,
    spike_highs: dict[int, Decimal] | None = None,
    volumes: list[Decimal] | None = None,
) -> tuple[DailyCandle, ...]:
    interval = BAR_MS[bar]
    last_ts = (NOW_MS - interval) // interval * interval
    first_ts = last_ts - (len(closes) - 1) * interval
    spikes = spike_highs or {}
    result: list[DailyCandle] = []
    for index, close in enumerate(closes):
        high = spikes.get(index, close + Decimal(5))
        result.append(
            DailyCandle(
                timestamp_ms=first_ts + index * interval,
                open=close - Decimal(2),
                high=high,
                low=close - Decimal(5),
                close=close,
                volume=volumes[index] if volumes else Decimal(100),
            )
        )
    return tuple(result)


def rising(start: Decimal, step: Decimal, count: int) -> list[Decimal]:
    return [start + step * index for index in range(count)]


def base_inputs(
    *,
    last_1h_close: Decimal = Decimal("80600"),
    spikes_4h: dict[int, Decimal] | None = None,
    volumes_1h: list[Decimal] | None = None,
) -> dict[str, object]:
    closes_1h = rising(Decimal(80000), Decimal(5), 119) + [last_1h_close]
    if volumes_1h is None:
        volumes_1h = [Decimal(100)] * 119 + [Decimal(300)]
    if spikes_4h is None:
        spikes_4h = {
            50: Decimal("80650"),  # strong level near the breakout
            60: Decimal("82000"),
            75: Decimal("83000"),
            90: Decimal("84500"),
        }
    return {
        "candles_1d": candles("1Dutc", rising(Decimal(60000), Decimal(170), 120)),
        "candles_4h": candles(
            "4H", rising(Decimal(78000), Decimal(20), 120), spike_highs=spikes_4h
        ),
        "candles_1h": candles("1H", closes_1h, volumes=volumes_1h),
        "candles_15m": candles("15m", rising(Decimal(80000), Decimal("0.5"), 120)),
        "now_ms": NOW_MS,
    }


def run(
    *,
    news_checked: bool = True,
    last_1h_close: Decimal = Decimal("80600"),
    spikes_4h: dict[int, Decimal] | None = None,
    volumes_1h: list[Decimal] | None = None,
) -> CandidateResult:
    inputs = base_inputs(
        last_1h_close=last_1h_close, spikes_4h=spikes_4h, volumes_1h=volumes_1h
    )
    return build_candidate(
        "BTC/USDT", news_checked=news_checked, **inputs  # type: ignore[arg-type]
    )
