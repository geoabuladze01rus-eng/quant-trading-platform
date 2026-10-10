import unittest
from decimal import Decimal

from quant_trading_platform.strategies.spot_momentum import BAR_MS, DailyCandle
from quant_trading_platform.strategies.spot_signal_generator import (
    CandidateResult,
    build_candidate,
    swing_highs,
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


class SwingHighTests(unittest.TestCase):
    def test_pivot_needs_lower_highs_on_both_sides(self) -> None:
        highs = [Decimal(v) for v in (1, 2, 3, 10, 3, 2, 1)]
        candles_ = tuple(
            DailyCandle(i, h, h, h, h, Decimal(1)) for i, h in enumerate(highs)
        )
        self.assertEqual(swing_highs(candles_, span=3), [Decimal(10)])


class BuildCandidateTests(unittest.TestCase):
    def test_complete_setup_is_very_high_with_news_checked(self) -> None:
        result = run()
        self.assertEqual(result.evaluation.grade, "VERY_HIGH")
        self.assertTrue(result.evaluation.deliverable)
        self.assertIsNotNone(result.plan)
        assert result.plan is not None
        self.assertEqual(result.plan.entry_low, Decimal("80595"))
        self.assertEqual(
            result.plan.targets,
            (Decimal("82000"), Decimal("83000"), Decimal("84500")),
        )
        self.assertLess(result.plan.stop_loss, result.plan.entry_low)
        self.assertGreaterEqual(result.evaluation.net_risk_reward or Decimal(0), Decimal(2))

    def test_unchecked_news_is_never_deliverable(self) -> None:
        result = run(news_checked=False)
        self.assertFalse(result.evaluation.deliverable)
        self.assertEqual(result.evaluation.grade, "NONE")
        self.assertIn("новостной фильтр не проверен", " ".join(result.evaluation.reasons))

    def test_price_above_entry_zone_is_rejected(self) -> None:
        result = run(last_1h_close=Decimal("80800"))
        self.assertFalse(result.evaluation.deliverable)
        self.assertIn("выше зоны входа", " ".join(result.evaluation.reasons))

    def test_no_breakout_is_not_deliverable(self) -> None:
        result = run(last_1h_close=Decimal("80590"))
        self.assertFalse(result.evaluation.deliverable)
        self.assertIn("структура 1H не подтверждена", " ".join(result.evaluation.reasons))

    def test_missing_three_resistances_is_rejected(self) -> None:
        result = run(spikes_4h={50: Decimal("80650")})
        self.assertFalse(result.evaluation.deliverable)
        self.assertIn("трёх уровней", " ".join(result.evaluation.reasons))

    def test_stale_candles_are_rejected_before_analysis(self) -> None:
        inputs = base_inputs()
        inputs["now_ms"] = NOW_MS + 10 * BAR_MS["1Dutc"]
        result = build_candidate("BTC/USDT", news_checked=True, **inputs)  # type: ignore[arg-type]
        self.assertFalse(result.evaluation.deliverable)
        self.assertTrue(result.evaluation.reasons[0].startswith("данные не подтверждены"))

    def test_too_few_candles_are_rejected(self) -> None:
        inputs = base_inputs()
        inputs["candles_1d"] = candles("1Dutc", rising(Decimal(60000), Decimal(170), 100))
        result = build_candidate("BTC/USDT", news_checked=True, **inputs)  # type: ignore[arg-type]
        self.assertFalse(result.evaluation.deliverable)
        self.assertTrue(result.evaluation.reasons[0].startswith("данные не подтверждены"))

    def test_gap_in_candles_is_rejected(self) -> None:
        inputs = base_inputs()
        bars = list(inputs["candles_1h"])  # type: ignore[call-overload]
        del bars[60]
        inputs["candles_1h"] = tuple(bars)
        result = build_candidate("BTC/USDT", news_checked=True, **inputs)  # type: ignore[arg-type]
        self.assertFalse(result.evaluation.deliverable)
        self.assertTrue(result.evaluation.reasons[0].startswith("данные не подтверждены"))

    def test_zero_volume_is_rejected_without_crashing(self) -> None:
        result = run(volumes_1h=[Decimal(0)] * 120)
        self.assertFalse(result.evaluation.deliverable)
        self.assertIn("нулевой объём", " ".join(result.evaluation.reasons))


if __name__ == "__main__":
    unittest.main()
