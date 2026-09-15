from decimal import Decimal

import pytest

from quant_trading_platform.paper_trading.residual_exposure import assess_residual_exposure


def test_matched_legs_are_flat_without_a_hedge() -> None:
    decision = assess_residual_exposure(
        buy_quantity="1.25", sell_quantity=Decimal("1.25"),
        mark_price_usd="100", max_unhedged_notional_usd="50",
    )
    assert decision.status == "flat"
    assert decision.action == "none"
    assert decision.residual_notional_usd == 0


@pytest.mark.parametrize(
    ("bought", "sold", "side"),
    [("1", ".8", "sell"), (".8", "1", "buy")],
)
def test_small_residual_requires_a_paper_only_protective_hedge(
    bought: str, sold: str, side: str
) -> None:
    decision = assess_residual_exposure(
        buy_quantity=bought, sell_quantity=sold,
        mark_price_usd="100", max_unhedged_notional_usd="25",
    )
    assert decision.status == "hedge_required"
    assert decision.action == "simulate_protective_hedge"
    assert decision.hedge_side == side
    assert decision.residual_notional_usd == Decimal("20")
    assert "paper" in decision.reason_code


def test_missing_mark_halts_instead_of_guessing_value() -> None:
    decision = assess_residual_exposure(
        buy_quantity="1", sell_quantity="0",
        mark_price_usd=None, max_unhedged_notional_usd="100",
    )
    assert decision.status == "halted"
    assert decision.residual_notional_usd is None
    assert decision.reason_code == "residual_mark_unavailable"


def test_large_residual_halts_instead_of_auto_hedging() -> None:
    decision = assess_residual_exposure(
        buy_quantity="2", sell_quantity="0",
        mark_price_usd="100", max_unhedged_notional_usd="50",
    )
    assert decision.status == "halted"
    assert decision.reason_code == "residual_exposure_limit_exceeded"


@pytest.mark.parametrize(
    "arguments",
    [
        {
            "buy_quantity": "-1",
            "sell_quantity": "0",
            "mark_price_usd": "100",
            "max_unhedged_notional_usd": "10",
        },
        {
            "buy_quantity": "1",
            "sell_quantity": "0",
            "mark_price_usd": "0",
            "max_unhedged_notional_usd": "10",
        },
        {
            "buy_quantity": "1",
            "sell_quantity": "0",
            "mark_price_usd": "NaN",
            "max_unhedged_notional_usd": "10",
        },
    ],
)
def test_invalid_input_is_rejected(arguments: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        assess_residual_exposure(**arguments)
