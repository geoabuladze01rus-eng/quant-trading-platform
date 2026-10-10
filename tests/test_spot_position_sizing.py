import unittest
from decimal import Decimal

from quant_trading_platform.strategies.spot_position_sizing import size_position
from quant_trading_platform.strategies.spot_signal_rules import TradePlan

PLAN = TradePlan(
    entry_low=Decimal("80000"),
    entry_high=Decimal("80000"),
    stop_loss=Decimal("79000"),
    targets=(Decimal("82000"), Decimal("83000"), Decimal("84000")),
    fee_rate=Decimal("0.001"),
    slippage_rate=Decimal("0.0005"),
)


class PositionSizingTests(unittest.TestCase):
    def test_default_risk_is_half_percent_of_capital(self) -> None:
        size = size_position(
            PLAN, trading_capital_usdt=Decimal("10000"), free_usdt=Decimal("10000")
        )
        self.assertIsNone(size.reason)
        self.assertEqual(size.limited_by, "risk")
        self.assertAlmostEqual(float(size.risk_usdt), 50.0, places=6)

    def test_risk_per_coin_includes_costs(self) -> None:
        size = size_position(
            PLAN, trading_capital_usdt=Decimal("10000"), free_usdt=Decimal("10000")
        )
        # Worst-case entry 80000 * 1.0015 and stop 79000 * 0.9985 leave 1,000 * ... per coin.
        effective_entry = Decimal("80000") * Decimal("1.0015")
        effective_stop = Decimal("79000") * Decimal("0.9985")
        expected_coins = Decimal("50") / (effective_entry - effective_stop)
        self.assertAlmostEqual(float(size.coins), float(expected_coins), places=8)

    def test_cash_caps_the_position(self) -> None:
        size = size_position(
            PLAN, trading_capital_usdt=Decimal("10000"), free_usdt=Decimal("500")
        )
        self.assertEqual(size.limited_by, "cash")
        self.assertLessEqual(size.notional_usdt, Decimal("500"))

    def test_risk_above_one_percent_is_refused(self) -> None:
        size = size_position(
            PLAN,
            trading_capital_usdt=Decimal("10000"),
            free_usdt=Decimal("10000"),
            risk_pct=Decimal("1.5"),
        )
        self.assertEqual(size.coins, 0)
        self.assertIsNotNone(size.reason)

    def test_order_below_minimum_is_refused(self) -> None:
        size = size_position(
            PLAN, trading_capital_usdt=Decimal("10000"), free_usdt=Decimal("5")
        )
        self.assertEqual(size.coins, 0)
        self.assertIn("минимума", size.reason or "")

    def test_invalid_stop_is_refused(self) -> None:
        bad = TradePlan(
            entry_low=Decimal("80000"),
            entry_high=Decimal("80000"),
            stop_loss=Decimal("81000"),
            targets=PLAN.targets,
            fee_rate=PLAN.fee_rate,
            slippage_rate=PLAN.slippage_rate,
        )
        size = size_position(bad, trading_capital_usdt=Decimal("10000"), free_usdt=Decimal("10000"))
        self.assertEqual(size.coins, 0)
        self.assertIsNotNone(size.reason)

    def test_values_are_decimal(self) -> None:
        size = size_position(
            PLAN, trading_capital_usdt=Decimal("10000"), free_usdt=Decimal("10000")
        )
        for value in (size.coins, size.notional_usdt, size.risk_usdt):
            self.assertIsInstance(value, Decimal)


if __name__ == "__main__":
    unittest.main()
