import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest

from quant_trading_platform.persistence import SQLitePaperStore, decimal_text


def test_initialization_seed_and_reopening_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "paper.sqlite3"
    store = SQLitePaperStore(path)
    store.initialize()
    store.seed_account()
    store.upsert_balance("paper-default", "USDT", Decimal("12.123456789123456789123456789"))
    store.seed_account()
    store.close()
    reopened = SQLitePaperStore(path)
    assert reopened.get_balance("paper-default", "USDT")["available"] == (  # type: ignore[index]
        "12.123456789123456789123456789")
    assert reopened.get_account("paper-default")["initial_balances"]["USDT"] == "10000"  # type: ignore[index]
    assert len(reopened.list_balances("paper-default")) == 3
    with reopened.transaction() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT typeof(available) FROM paper_balances").fetchone()[0] == "text"
    reopened.close()


def test_all_entities_and_idempotency_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    store = SQLitePaperStore(path)
    store.seed_account()
    with store.transaction() as conn:
        for insert in (store.insert_order, store.insert_fill, store.insert_position,
                       store.insert_reconciliation, store.insert_audit):
            insert({"id": "stable", "amount": Decimal("1.000000000000000000000000001"),
                    "execution_id": "execution-1", "status": "filled"}, conn=conn)
        assert store.reserve_idempotency("paper-default", "request", "hash", conn=conn)
        assert not store.reserve_idempotency("paper-default", "request", "other", conn=conn)
        store.complete_idempotency("paper-default", "request", {"order_id": "stable"}, conn=conn)
    store.close()
    reopened = SQLitePaperStore(path)
    for getter in (reopened.get_order, reopened.get_fill, reopened.get_position,
                   reopened.get_reconciliation, reopened.get_audit):
        row = getter("stable")
        assert row is not None
        assert row["amount"] == "1.000000000000000000000000001"
        assert row["execution_id"] == "execution-1"
    record = reopened.get_idempotency("paper-default", "request")
    assert record is not None and record["response"] == {"order_id": "stable"}
    reopened.close()


def test_rollback_and_account_foreign_keys(tmp_path: Path) -> None:
    store = SQLitePaperStore(tmp_path / "paper.db")
    store.seed_account()
    with pytest.raises(RuntimeError), store.transaction() as conn:
        store.upsert_balance("paper-default", "USDT", Decimal("0"), conn=conn)
        store.insert_order({"id": "rolled-back"}, conn=conn)
        store.reserve_idempotency("paper-default", "rollback", "hash", conn=conn)
        raise RuntimeError("simulated crash")
    assert store.get_order("rolled-back") is None
    assert store.get_idempotency("paper-default", "rollback") is None
    assert store.get_balance("paper-default", "USDT")["available"] == "10000"  # type: ignore[index]
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_fill({"account_id": "missing"})
    store.close()


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_nonfinite_money_is_rejected(value: Decimal) -> None:
    with pytest.raises(ValueError):
        decimal_text(value)


def test_canonical_money_and_float_rejection() -> None:
    assert decimal_text(Decimal("1.2300")) == "1.23"
    assert decimal_text(Decimal("-0.000")) == "0"
    assert decimal_text(Decimal("1E+20")) == "100000000000000000000"
    store = SQLitePaperStore(":memory:")
    store.seed_account()
    with pytest.raises(ValueError):
        store.insert_order({"amount": 0.1})
    store.close()


def test_isolated_memory_stores_and_concurrent_transactions() -> None:
    first, second = SQLitePaperStore(":memory:"), SQLitePaperStore(":memory:")
    first.seed_account()
    assert second.list_accounts() == []

    def write(index: int) -> None:
        first.insert_order({"id": str(index)})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(20)))
    assert len(first.list_orders()) == 20
    first.close()
    second.close()


def test_updates_and_audit_filters() -> None:
    store = SQLitePaperStore(":memory:")
    store.seed_account()
    store.insert_order({"id": "order", "status": "pending"})
    store.update_order({"id": "order", "status": "filled"})
    assert store.get_order("order")["status"] == "filled"  # type: ignore[index]
    store.upsert_position({"id": "paper-default:BTC", "quantity": Decimal("1")})
    store.upsert_position({"id": "paper-default:BTC", "quantity": Decimal("2")})
    assert len(store.list_positions()) == 1
    for index in range(3):
        store.insert_audit({"id": str(index), "event_type": "filled", "order_id": "order"})
    assert store.count_audit(event_type="filled", order_id="order") == 3
    assert store.list_audit(limit=1, offset=1)[0]["id"] == "1"
    assert store.count_audit(event_type="' OR 1=1 --") == 0
    store.close()


def test_file_transactions_reserve_idempotency_once(tmp_path: Path) -> None:
    store = SQLitePaperStore(tmp_path / "concurrent.db")
    store.seed_account()

    def reserve(index: int) -> bool:
        return store.reserve_idempotency("paper-default", "same-key", f"hash-{index}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(reserve, range(12)))
    assert outcomes.count(True) == 1
    store.close()


@pytest.mark.parametrize("key", ["", "   ", "contains\x00nul", "x" * 129])
def test_invalid_idempotency_keys_rejected(key: str) -> None:
    store = SQLitePaperStore(":memory:")
    store.seed_account()
    with pytest.raises(ValueError):
        store.reserve_idempotency("paper-default", key, "hash")
    store.close()


def test_databases_are_isolated_and_schema_has_no_real_money_columns(tmp_path: Path) -> None:
    first, second = SQLitePaperStore(tmp_path / "one.db"), SQLitePaperStore(tmp_path / "two.db")
    first.seed_account()
    assert second.list_accounts() == []
    with first.transaction() as db:
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert len(tables) == 8
        for table in tables:
            # Table names originate from this test's schema, never user input.
            columns = db.execute(f"PRAGMA table_info({table[0]})").fetchall()
            names = {row[1] for row in columns}
            assert {"updated_at", "status", "correlation_id", "schema_version"} <= names
            assert all(row[2] != "REAL" for row in columns)
    first.close()
    second.close()
