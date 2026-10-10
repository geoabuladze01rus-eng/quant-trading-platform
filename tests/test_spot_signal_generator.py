import unittest
from decimal import Decimal

from spot_fixtures import NOW_MS, base_inputs, candles, rising, run

from quant_trading_platform.strategies.spot_momentum import BAR_MS, DailyCandle
from quant_trading_platform.strategies.spot_signal_generator import (
    build_candidate,
    swing_highs,
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
