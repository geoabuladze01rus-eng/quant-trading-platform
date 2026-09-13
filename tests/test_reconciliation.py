from decimal import Decimal

from test_paper_accounting import execute, service

from quant_trading_platform.paper_trading.reconciliation import reconcile_records


def test_reconciliation_detects_balance_and_reservation_corruption(tmp_path):
    instance = service(tmp_path / "paper.db")
    execute(instance)
    instance.store.upsert_balance("paper-default", "USDT", Decimal(9999), Decimal(1))
    result = instance.reconcile()
    codes = {issue["reason_code"] for issue in result["issues"]}
    assert {"balance_fill_mismatch", "reservation_mismatch"} <= codes
    assert all(issue["human_reason"] and issue["severity"] == "error" for issue in result["issues"])


def test_reconciliation_orphan_negative_and_status_issues():
    result = reconcile_records(
        {"account_id": "a", "initial_balances": {"USDT": "100", "BTC": "0"}},
        [{"asset": "USDT", "available": "-1", "reserved": "0", "total": "2"}],
        [],
        [
            {
                "order_id": "missing",
                "symbol": "BTC/USDT",
                "side": "buy",
                "quantity": "1",
                "notional_usd": "100",
                "fee_usd": "1",
            }
        ],
        [],
    )
    codes = {issue["reason_code"] for issue in result["issues"]}
    assert {"orphan_fill", "negative_balance", "balance_total_mismatch", "missing_balance"} <= codes
    assert (
        next(issue for issue in result["issues"] if issue["reason_code"] == "orphan_fill")[
            "correlation_id"
        ]
        == "missing"
    )


def test_read_only_reconcile_and_explicit_persistence(tmp_path):
    instance = service(tmp_path / "paper.db")
    execute(instance)
    assert instance.reconcile()["status"] == "ok"
    assert instance.store.list_reconciliations() == []
    assert instance.reconcile(persist=True)["status"] == "ok"
    assert len(instance.store.list_reconciliations()) == 1
