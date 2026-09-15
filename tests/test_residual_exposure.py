from decimal import Decimal

import pytest

from quant_trading_platform.paper_trading.residual_exposure import (
    PaperLegEvent,
    assess_leg_events,
    assess_residual_exposure,
)


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
        mark_source="public_order_book", mark_timestamp_ms=1_000, now_ms=1_100,
    )
    assert decision.status == "hedge_required"
    assert decision.action == "review_paper_hedge_proposal"
    assert decision.hedge_side == side
    assert decision.residual_notional_usd == Decimal("20")
    assert "paper" in decision.reason_code
    assert decision.as_dict()["hedge_proposal"]["confirmable"] is False
    assert decision.mark_age_ms == 100


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
        mark_source="public_order_book", mark_timestamp_ms=1_000, now_ms=1_100,
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


def _event(event_id: str, side: str, quantity: str) -> PaperLegEvent:
    return PaperLegEvent(
        "group-1", event_id, side, "binance" if side == "buy" else "okx",
        f"paper-{event_id}", Decimal(quantity), 1_000,
    )


def test_independent_buy_only_sell_only_and_multi_partial_events() -> None:
    for events, expected_side, expected_qty in (
        ([_event("buy", "buy", "1")], "sell", Decimal("1")),
        ([_event("sell", "sell", "1")], "buy", Decimal("-1")),
        ([_event("buy-1", "buy", ".4"), _event("buy-2", "buy", ".6"),
          _event("sell", "sell", ".7")], "sell", Decimal(".3")),
    ):
        decision = assess_leg_events(
            events, mark_price_usd="100", max_unhedged_notional_usd="100",
            mark_source="public_order_book", mark_timestamp_ms=1_000, now_ms=1_100,
        )
        assert decision.status == "hedge_required"
        assert decision.hedge_side == expected_side
        assert decision.residual_quantity == expected_qty
        assert decision.as_dict()["hedge_proposal"]["execution_enabled"] is False


def test_missing_stale_future_mark_or_reconciliation_mismatch_halts_without_proposal() -> None:
    events = [_event("buy", "buy", "1")]
    for kwargs, code in (
        ({"mark_price_usd": None}, "residual_mark_unavailable"),
        ({"mark_price_usd": "100", "mark_source": None}, "residual_mark_unavailable"),
        ({"mark_price_usd": "100", "mark_source": "public_order_book",
          "mark_timestamp_ms": 1_000, "now_ms": 2_100}, "residual_mark_stale"),
        ({"mark_price_usd": "100", "mark_source": "public_order_book",
          "mark_timestamp_ms": 2_000, "now_ms": 1_000}, "residual_mark_stale"),
        ({"mark_price_usd": "100", "mark_source": "public_order_book",
          "mark_timestamp_ms": 1_000, "now_ms": 1_100,
          "reconciliation_ok": False}, "residual_accounting_mismatch"),
    ):
        decision = assess_leg_events(
            events, max_unhedged_notional_usd="100", **kwargs,
        )
        assert decision.status == "halted"
        assert decision.reason_code == code
        assert decision.as_dict()["hedge_proposal"] is None


def test_cap_breach_and_replay_are_deterministic_and_auditable() -> None:
    buy = _event("buy", "buy", "1")
    kwargs = {
        "mark_price_usd": "100", "max_unhedged_notional_usd": "50",
        "mark_source": "public_order_book", "mark_timestamp_ms": 1_000,
        "now_ms": 1_100,
    }
    first = assess_leg_events([buy, buy], **kwargs)
    restarted = assess_leg_events([buy], **kwargs)
    assert first == restarted
    assert first.status == "halted"
    assert first.reason_code == "residual_exposure_limit_exceeded"
    audit = first.audit_payload(
        execution_group_id="group-1", strategy="cross_venue_spread", timestamp_ms=1_100
    )
    assert {"reason_code", "human_reason", "mark_source", "mark_age_ms",
            "configured_cap_usd", "source_event_ids", "residual_notional_usd",
            "paper_only", "live_execution"} <= audit.keys()
    assert audit["source_event_ids"] == ("buy",)
    assert audit["live_execution"] is False


def test_conflicting_event_id_float_and_group_mixing_fail_closed() -> None:
    with pytest.raises(ValueError, match="conflicting"):
        assess_leg_events(
            [_event("same", "buy", ".5"), _event("same", "buy", ".6")],
            mark_price_usd="100", max_unhedged_notional_usd="100",
        )
    with pytest.raises(ValueError):
        assess_residual_exposure(
            buy_quantity=1.0, sell_quantity="0", mark_price_usd="100",
            max_unhedged_notional_usd="100",
        )
    with pytest.raises(ValueError, match="one execution group"):
        assess_leg_events(
            [_event("first", "buy", "1"),
             PaperLegEvent("group-2", "second", "sell", "okx", "paper-2",
                           Decimal("1"), 1_000)],
            mark_price_usd="100", max_unhedged_notional_usd="100",
        )
