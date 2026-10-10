import unittest
from decimal import Decimal

from quant_trading_platform.strategies.spot_signal_rules import (
    MarketContext,
    TradePlan,
    evaluate,
    net_risk_reward,
)

FOUR = frozenset({"trend_4h_1h", "strong_level", "volume", "structure"})
SEVEN = FOUR | {"retest_hold", "ema_vwap", "momentum"}


def plan(**overrides: object) -> TradePlan:
    base: dict[str, object] = {
        "entry_low": Decimal("82000"),
        "entry_high": Decimal("82400"),
        "stop_loss": Decimal("80500"),
        "targets": (Decimal("88000"), Decimal("90000"), Decimal("93000")),
        "fee_rate": Decimal("0.001"),
        "slippage_rate": Decimal("0.0005"),
    }
    base.update(overrides)
    return TradePlan(**base)  # type: ignore[arg-type]


def context(**overrides: object) -> MarketContext:
    base: dict[str, object] = {
        "trend_4h_up": True,
        "trend_1d_not_strong_down": True,
        "structure_1h_confirmed": True,
        "data_fresh": True,
        "sources_verified": True,
        "news_risk_critical": False,
        "liquidity_ok": True,
        "volume_confirmed": True,
        "multi_timeframe_aligned": False,
        "confirmations": FOUR,
    }
    base.update(overrides)
    return MarketContext(**base)  # type: ignore[arg-type]


PRICE = Decimal("82100")


class SpotSignalRulesTests(unittest.TestCase):
    def test_high_when_all_gates_pass(self) -> None:
        result = evaluate(plan(), context(), current_price=PRICE)
        self.assertEqual(result.grade, "HIGH")
        self.assertTrue(result.deliverable)
        self.assertGreaterEqual(result.net_risk_reward or Decimal(0), Decimal("2"))

    def test_very_high_needs_seven_categories_and_alignment(self) -> None:
        result = evaluate(
            plan(),
            context(confirmations=SEVEN, multi_timeframe_aligned=True),
            current_price=PRICE,
        )
        self.assertEqual(result.grade, "VERY_HIGH")
        self.assertTrue(result.deliverable)

    def test_seven_confirmations_without_alignment_stays_high(self) -> None:
        result = evaluate(plan(), context(confirmations=SEVEN), current_price=PRICE)
        self.assertEqual(result.grade, "HIGH")

    def test_medium_is_never_deliverable(self) -> None:
        result = evaluate(plan(), context(liquidity_ok=False), current_price=PRICE)
        self.assertEqual(result.grade, "MEDIUM")
        self.assertFalse(result.deliverable)

    def test_fewer_than_four_confirmations_is_none(self) -> None:
        result = evaluate(
            plan(), context(confirmations=frozenset({"volume"})), current_price=PRICE
        )
        self.assertEqual(result.grade, "NONE")
        self.assertIn("подтверждений 1", " ".join(result.reasons))

    def test_risk_reward_below_two_blocks_delivery(self) -> None:
        near_target = plan(targets=(Decimal("82800"), Decimal("84000"), Decimal("86000")))
        result = evaluate(near_target, context(), current_price=PRICE)
        self.assertFalse(result.deliverable)
        self.assertIn("ниже минимума 1:2", " ".join(result.reasons))

    def test_fees_and_slippage_reduce_reward(self) -> None:
        gross = net_risk_reward(plan(fee_rate=Decimal(0), slippage_rate=Decimal(0)))
        net = net_risk_reward(plan())
        self.assertLess(net, gross)

    def test_downtrend_4h_blocks_regardless_of_other_factors(self) -> None:
        result = evaluate(
            plan(), context(trend_4h_up=False, confirmations=SEVEN), current_price=PRICE
        )
        self.assertFalse(result.deliverable)
        self.assertIn("против тренда", " ".join(result.reasons))

    def test_stale_or_unverified_data_blocks(self) -> None:
        stale = evaluate(plan(), context(data_fresh=False), current_price=PRICE)
        self.assertFalse(stale.deliverable)
        unverified = evaluate(plan(), context(sources_verified=False), current_price=PRICE)
        self.assertFalse(unverified.deliverable)

    def test_critical_news_blocks(self) -> None:
        result = evaluate(plan(), context(news_risk_critical=True), current_price=PRICE)
        self.assertFalse(result.deliverable)

    def test_price_above_entry_zone_blocks(self) -> None:
        result = evaluate(plan(), context(), current_price=Decimal("83000"))
        self.assertFalse(result.deliverable)
        self.assertIn("завышенной", " ".join(result.reasons))

    def test_targets_must_ascend(self) -> None:
        bad = plan(targets=(Decimal("88000"), Decimal("86000"), Decimal("90000")))
        result = evaluate(bad, context(), current_price=PRICE)
        self.assertFalse(result.deliverable)
        self.assertIn("возрастать", " ".join(result.reasons))

    def test_unknown_confirmation_category_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate(plan(), context(confirmations=frozenset({"made_up"})), current_price=PRICE)


if __name__ == "__main__":
    unittest.main()
