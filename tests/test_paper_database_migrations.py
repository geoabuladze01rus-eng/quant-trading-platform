import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from quant_trading_platform.persistence.migrations import (
    MIGRATIONS,
    SCHEMA_VERSION,
    Migration,
    SchemaValidationError,
    SchemaVersionError,
    apply_migrations,
)
from quant_trading_platform.persistence.store import SQLitePaperStore

EXACT_DECIMAL = "123456789.123456789123456789123456789"


def create_v1_database(path: Path) -> None:
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        MIGRATIONS[0].apply(connection)
        connection.execute("PRAGMA user_version=1")
        payload = json.dumps({"initial_balances": {"USDT": EXACT_DECIMAL}})
        connection.execute(
            """INSERT INTO paper_accounts
            (id,name,status,created_at,schema_version,updated_at,correlation_id,payload)
            VALUES (?,?,?,?,?,?,?,?)""",
            ("paper-default", "Legacy", "active", "before", 1, "before", "", payload),
        )
        connection.execute(
            """INSERT INTO paper_balances
            (account_id,asset,available,reserved,updated_at,created_at,status,
             correlation_id,schema_version) VALUES (?,?,?,?,?,?,?,?,?)""",
            ("paper-default", "USDT", EXACT_DECIMAL, "0", "before", "before", "active", "", 1),
        )
        order_payload = json.dumps(
            {
                "id": "legacy-order",
                "account_id": "paper-default",
                "execution_id": "legacy-execution",
                "status": "filled",
                "created_at": "before",
                "updated_at": "before",
                "correlation_id": "legacy",
                "schema_version": 1,
                "notional_usdt": EXACT_DECIMAL,
            }
        )
        connection.execute(
            """INSERT INTO paper_orders
            (id,account_id,execution_id,status,created_at,schema_version,updated_at,
             correlation_id,payload) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "legacy-order",
                "paper-default",
                "legacy-execution",
                "filled",
                "before",
                1,
                "before",
                "legacy",
                order_payload,
            ),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def test_v1_migrates_with_records_and_decimal_text_unchanged(tmp_path: Path) -> None:
    database = tmp_path / "legacy-v1.sqlite3"
    create_v1_database(database)

    store = SQLitePaperStore(database)
    balance = store.get_balance("paper-default", "USDT")
    account = store.get_account("paper-default")
    order = store.get_order("legacy-order")

    assert balance is not None and balance["available"] == EXACT_DECIMAL
    assert Decimal(balance["available"]) == Decimal(EXACT_DECIMAL)
    assert account is not None
    assert account["initial_balances"]["USDT"] == EXACT_DECIMAL
    assert order is not None and order["notional_usdt"] == EXACT_DECIMAL
    with store.transaction() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert [tuple(row) for row in connection.execute(
            "SELECT version,name FROM schema_migrations ORDER BY version"
        )] == [
            (1, "create_paper_ledger"),
            (2, "add_schema_migration_history"),
        ]
    store.close()


def test_repeated_migration_run_is_a_noop(tmp_path: Path) -> None:
    database = tmp_path / "repeat.sqlite3"
    create_v1_database(database)

    first = SQLitePaperStore(database)
    first.close()
    before = database.read_bytes()
    second = SQLitePaperStore(database)
    second.close()

    assert database.read_bytes() == before


def test_failed_migration_rolls_back_schema_and_version(tmp_path: Path) -> None:
    database = tmp_path / "rollback.sqlite3"
    store = SQLitePaperStore(database)
    store.close()

    def fail(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE should_rollback(value TEXT)")
        raise RuntimeError("injected migration failure")

    connection = sqlite3.connect(database, isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        with pytest.raises(RuntimeError, match="injected"):
            apply_migrations(
                connection,
                migrations=(*MIGRATIONS, Migration(3, "injected_failure", fail)),
            )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='should_rollback'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_future_schema_is_rejected_without_modifying_database(tmp_path: Path) -> None:
    database = tmp_path / "future.sqlite3"
    store = SQLitePaperStore(database)
    store.close()
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version=999")
    connection.commit()
    connection.close()
    before = database.read_bytes()

    with pytest.raises(SchemaVersionError, match="newer"):
        SQLitePaperStore(database)

    assert database.read_bytes() == before


def test_unversioned_nonempty_database_is_not_guessed(tmp_path: Path) -> None:
    database = tmp_path / "unknown.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE paper_accounts(id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    with pytest.raises(SchemaValidationError, match="Unversioned"):
        SQLitePaperStore(database)
