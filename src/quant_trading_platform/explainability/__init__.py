"""Transparent explanations of deterministic paper-trading gates."""

from collections.abc import Mapping

from quant_trading_platform.explainability.reasons import human_reason
from quant_trading_platform.models import ArbitrageOpportunity
from quant_trading_platform.paper_trading import PaperExecutionReport
from quant_trading_platform.risk import RiskDecision, reason_code_for


def explain_advanced_decision(event: Mapping[str, object]) -> dict[str, object]:
    """Structured audit explanation with the stable Russian reason catalog."""
    from quant_trading_platform.audit_log.persistent import advanced_decision

    return advanced_decision(event)


def explain_risk_decision(decision: RiskDecision) -> dict[str, object]:
    return {
        "summary": decision.reason_text,
        "approved": decision.approved,
        "reason_code": decision.reason_code,
        "reason_text": decision.reason_text,
        "human_reason": human_reason(decision.reason_code),
        "risk_score": "passed" if decision.approved else "blocked",
        "risk_score_definition": "Deterministic gate status; not a probability of loss or profit.",
        "checks": list(decision.checks),
    }


def explain_opportunity(
    opportunity: ArbitrageOpportunity, decision: RiskDecision
) -> dict[str, object]:
    explanation = explain_risk_decision(decision)
    explanation.update({
        "summary": (
            f"{opportunity.symbol}: buy on {opportunity.buy_exchange.value}, "
            f"sell on {opportunity.sell_exchange.value}. "
            f"Gross edge {opportunity.expected_gross_pct:.4f}%, fees {opportunity.fees_pct:.2f}%, "
            f"slippage {opportunity.slippage_pct:.2f}%; "
            f"expected net edge {opportunity.expected_net_pct:.4f}%. {decision.reason_text}"
        ),
        "gross_edge_pct": str(opportunity.expected_gross_pct),
        "fees_pct": str(opportunity.fees_pct),
        "slippage_pct": str(opportunity.slippage_pct),
        "net_edge_pct": str(opportunity.expected_net_pct),
        "market_type": opportunity.market_type.value,
    })
    return explanation


def explain_paper_execution(
    execution: PaperExecutionReport | Mapping[str, object],
    decision: RiskDecision | None = None,
) -> dict[str, object]:
    """Accept a typed execution view or its serialized API representation."""
    if isinstance(execution, Mapping):
        status = str(execution.get("status", "unknown"))
        reason = str(execution.get("reason_text") or execution.get("reason") or status)
        execution_id = str(execution.get("execution_id") or execution.get("id") or "")
        code = str(execution.get("reason_code") or "")
    else:
        status, reason = execution.status, execution.reason_text
        execution_id = execution.execution_id
        code = execution.reason_code
    approved = status.lower() in {"filled", "completed", "approved", "simulated"}
    explanation = explain_risk_decision(
        decision or RiskDecision(
            approved, reason, reason_code=code or reason_code_for(reason, approved)
        )
    )
    explanation.update({
        "summary": f"Paper execution {status}: {reason}. No live order was submitted.",
        "status": status,
        "execution_id": execution_id,
        "paper_only": True,
    })
    return explanation
