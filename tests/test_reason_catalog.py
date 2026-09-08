from quant_trading_platform.explainability.reasons import REASON_CATALOG, human_reason


def test_required_reason_catalog_is_stable_and_russian():
    codes = {
        "stale_market_data", "insufficient_net_edge", "market_mismatch",
        "insufficient_paper_balance", "duplicate_idempotency_key", "invalid_order",
        "depth_insufficient", "risk_limit_exceeded", "paper_order_filled",
        "paper_order_partially_filled", "paper_order_rejected", "reconciliation_mismatch",
        "source_unavailable", "unsupported_market",
    }
    assert codes <= REASON_CATALOG.keys()
    for code in codes:
        assert any("а" <= character <= "я" for character in human_reason(code).lower())
    assert human_reason("secret-token-unknown") == human_reason("unknown")
    assert human_reason("paper_filled") == human_reason("paper_order_filled")
