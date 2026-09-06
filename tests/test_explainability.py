from dataclasses import FrozenInstanceError
from decimal import Decimal
from time import time

import pytest

from quant_trading_platform.audit_log import AuditLog
from quant_trading_platform.config import MarketScope, Settings
from quant_trading_platform.explainability import (
    explain_opportunity,
    explain_paper_execution,
    explain_risk_decision,
)
from quant_trading_platform.models import ArbitrageOpportunity, Venue
from quant_trading_platform.paper_trading import PaperExecutionEngine
from quant_trading_platform.risk import RiskDecision, RiskEngine, RiskLimits


@pytest.mark.parametrize(("reason", "code"), [
    ("Market data is stale", "stale_market_data"),
    ("Expected net profit is below minimum threshold", "insufficient_edge_after_costs"),
    ("Opportunity exceeds per-trade notional limit", "notional_limit_exceeded"),
    ("Venue and market type mismatch", "market_type_mismatch"),
    ("API error: trading paused", "venue_unavailable"),
    ("Live trading is locked", "live_trading_locked"),
    ("Insufficient liquidity", "insufficient_depth"),
])
def test_legacy_decisions_gain_machine_codes(reason: str, code: str) -> None:
    decision = RiskDecision(False, reason, ("legacy_check",))
    assert decision.reason == decision.reason_text == decision.explanation == reason
    assert decision.reason_code == code
    assert decision.checks == ("legacy_check",)
    assert explain_risk_decision(decision)["risk_score"] == "blocked"


def test_opportunity_explanation_discloses_costs_and_gate_status() -> None:
    opportunity = ArbitrageOpportunity(
        "spread", "BTC/USDT", Venue.BINANCE, Venue.BYBIT,
        Decimal("0.5"), Decimal("0.2"), Decimal("50"), int(time() * 1000),
        fees_pct=Decimal("0.2"), slippage_pct=Decimal("0.1"),
    )
    decision = RiskEngine(RiskLimits()).evaluate(opportunity)
    result = explain_opportunity(opportunity, decision)
    assert result["risk_score"] == "passed"
    assert result["reason_code"] == "approved"
    assert result["net_edge_pct"] == "0.2"
    assert "fees 0.20%, slippage 0.10%" in str(result["summary"])
    assert "not a probability" in str(result["risk_score_definition"])


def test_summary_formats_long_decimals_without_changing_raw_metrics() -> None:
    net = Decimal("-0.2567358967162503508279539714")
    gross = Decimal("0.0432641032837496491720460286")
    opportunity = ArbitrageOpportunity(
        "spread", "BTC/USDT", Venue.BINANCE, Venue.BYBIT,
        gross, net, Decimal("50"), int(time() * 1000),
        fees_pct=Decimal("0.20"), slippage_pct=Decimal("0.10"),
    )
    result = explain_opportunity(opportunity, RiskDecision(False, "Insufficient edge"))
    assert "Gross edge 0.0433%, fees 0.20%, slippage 0.10%" in str(result["summary"])
    assert "expected net edge -0.2567%" in str(result["summary"])
    assert result["net_edge_pct"] == str(net)
    assert result["gross_edge_pct"] == str(gross)
    assert opportunity.expected_net_pct == net


def test_execution_explanation_is_explicitly_paper_only() -> None:
    result = explain_paper_execution({
        "id": "exec-1", "status": "rejected", "reason": "Insufficient depth",
    })
    assert result["execution_id"] == "exec-1"
    assert result["reason_code"] == "insufficient_depth"
    assert result["paper_only"] is True
    assert "No live order" in str(result["summary"])


def test_actual_engine_report_explanation() -> None:
    opportunity = ArbitrageOpportunity(
        "spread", "BTC/USDT", Venue.BINANCE, Venue.BYBIT,
        Decimal("0.5"), Decimal("0.2"), Decimal("50"), int(time() * 1000),
        fees_pct=Decimal("0.2"), slippage_pct=Decimal("0.1"),
    )
    report = PaperExecutionEngine().simulate(
        opportunity, None, None, notional_usd=Decimal("10"), settings=Settings(),
    )
    explanation = explain_paper_execution(report)
    assert explanation["reason_text"] == report.reason_text
    assert explanation["reason_code"] == report.reason_code
    assert explanation["execution_id"] == report.execution_id
    assert explanation["risk_score"] == "blocked"


def test_audit_events_are_immutable_and_listing_is_detached() -> None:
    log = AuditLog()
    item = log.record(
        "risk_rejected", "Market data is stale", MarketScope.CRYPTO, "spread",
        who="local_paper_user", decision="rejected", execution_id="exec-1",
        reason_code="stale_market_data", opportunity_id="opp-1",
    )
    assert item.when == item.timestamp
    assert item.why == item.reason
    with pytest.raises(FrozenInstanceError):
        item.reason = "changed"  # type: ignore[misc]
    snapshot = log.list()
    snapshot[0]["reason"] = "changed"
    snapshot.clear()
    assert log.list()[0]["reason"] == "Market data is stale"
    assert log.list()[0]["opportunity_id"] == "opp-1"


@pytest.mark.parametrize("event", [
    "opportunity_detected", "risk_approved", "risk_rejected", "paper_order_created",
    "paper_order_rejected", "paper_fill_simulated", "reconciliation_completed",
])
def test_audit_records_lifecycle_events(event: str) -> None:
    log = AuditLog()
    item = log.record(event, "Paper lifecycle", MarketScope.CRYPTO, "spread")
    assert log.list()[0]["event"] == event
    assert item.who == "system"
    assert item.when


def test_audit_retention_is_bounded() -> None:
    log = AuditLog()
    for index in range(10_001):
        log.record("opportunity_detected", str(index), MarketScope.CRYPTO, "spread")
    snapshot = log.list()
    assert len(snapshot) == 10_000
    assert snapshot[0]["reason"] == "10000"
    assert snapshot[-1]["reason"] == "1"
