from dataclasses import replace
from decimal import Decimal
from time import time

import pytest

from quant_trading_platform.models import ArbitrageOpportunity, MarketType, Venue
from quant_trading_platform.risk import RiskEngine, RiskLimits


def opportunity() -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        strategy="cross_venue_spread", symbol="BTC/USDT", buy_exchange=Venue.BINANCE,
        sell_exchange=Venue.OKX, expected_gross_pct=Decimal("1"),
        expected_net_pct=Decimal("0.7"), max_notional_usd=Decimal("100"),
        detected_at_ms=int(time() * 1000), fees_pct=Decimal("0.2"), slippage_pct=Decimal("0.1"),
    )


def test_fresh_opportunity_can_pass() -> None:
    assert RiskEngine(RiskLimits()).evaluate(opportunity()).approved


def test_old_quote_cannot_be_made_fresh_by_detection_or_age_override() -> None:
    old = replace(opportunity(), source_timestamp_ms=int(time() * 1000) - 10_000)
    decision = RiskEngine(RiskLimits()).evaluate(old, data_age_ms=0)
    assert not decision.approved
    assert "stale" in decision.reason


@pytest.mark.parametrize("api_error,balance_mismatch,reason", [
    (True, False, "API error"), (False, True, "Balance mismatch"),
])
def test_failed_operational_gates(api_error: bool, balance_mismatch: bool, reason: str) -> None:
    decision = RiskEngine(RiskLimits()).evaluate(
        opportunity(), api_error=api_error, balance_mismatch=balance_mismatch,
    )
    assert not decision.approved
    assert reason in decision.reason


@pytest.mark.parametrize("loss", ["2", "2.01", "NaN", "Infinity", "-1"])
def test_loss_limit_or_invalid_loss_rejects(loss: str) -> None:
    decision = RiskEngine(RiskLimits()).evaluate(opportunity(), daily_loss_pct=Decimal(loss))
    assert not decision.approved


@pytest.mark.parametrize("notional", ["100.01", "0", "-1", "NaN", "Infinity"])
def test_invalid_or_excess_notional_rejected(notional: str) -> None:
    assert not RiskEngine(RiskLimits()).evaluate(
        replace(opportunity(), max_notional_usd=Decimal(notional)),
    ).approved


@pytest.mark.parametrize("edge", ["0.09", "0", "-0.2", "NaN", "Infinity"])
def test_weak_negative_or_invalid_net_edge_rejected(edge: str) -> None:
    assert not RiskEngine(RiskLimits()).evaluate(
        replace(opportunity(), expected_net_pct=Decimal(edge)),
    ).approved


def test_net_edge_cannot_omit_costs() -> None:
    decision = RiskEngine(RiskLimits()).evaluate(
        replace(opportunity(), expected_net_pct=Decimal("1")),
    )
    assert not decision.approved
    assert "costs" in decision.reason


def test_explicit_strategy_rejection_is_binding() -> None:
    decision = RiskEngine(RiskLimits()).evaluate(
        replace(opportunity(), rejection_reason="Insufficient liquidity"),
    )
    assert not decision.approved
    assert decision.reason == "Insufficient liquidity"


def test_mislabeled_market_rejected_by_risk() -> None:
    assert not RiskEngine(RiskLimits()).evaluate(
        replace(opportunity(), market_type=MarketType.RUSSIAN_STOCKS),
    ).approved


def test_future_quote_timestamp_rejected() -> None:
    assert not RiskEngine(RiskLimits()).evaluate(
        replace(opportunity(), source_timestamp_ms=int(time() * 1000) + 10_000),
    ).approved


@pytest.mark.parametrize("limit", ["0", "-1", "NaN", "Infinity", "2.01"])
def test_invalid_daily_risk_limit_rejected(limit: str) -> None:
    with pytest.raises(ValueError):
        RiskLimits(max_daily_loss_pct=Decimal(limit))
