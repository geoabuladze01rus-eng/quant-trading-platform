"""Ordered, transactional migrations for the local paper SQLite ledger."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

SCHEMA_VERSION = 2

ENTITY_TABLES = (
    "paper_orders",
    "paper_fills",
    "paper_positions",
    "reconciliation_results",
    "audit_events",
)
V1_TABLES = frozenset(
    {"paper_accounts", "paper_balances", "idempotency_records", *ENTITY_TABLES}
)
V2_TABLES = V1_TABLES | {"schema_migrations"}

_V1_COLUMNS = {
    "paper_accounts": {
        "id": "TEXT",
        "name": "TEXT",
        "status": "TEXT",
        "created_at": "TEXT",
        "schema_version": "INTEGER",
        "updated_at": "TEXT",
        "correlation_id": "TEXT",
        "payload": "TEXT",
    },
    "paper_balances": {
        "account_id": "TEXT",
        "asset": "TEXT",
        "available": "TEXT",
        "reserved": "TEXT",
        "updated_at": "TEXT",
        "created_at": "TEXT",
        "status": "TEXT",
        "correlation_id": "TEXT",
        "schema_version": "INTEGER",
    },
    **{
        table: {
            "id": "TEXT",
            "account_id": "TEXT",
            "execution_id": "TEXT",
            "status": "TEXT",
            "created_at": "TEXT",
            "schema_version": "INTEGER",
            "updated_at": "TEXT",
            "correlation_id": "TEXT",
            "payload": "TEXT",
        }
        for table in ENTITY_TABLES
    },
    "idempotency_records": {
        "account_id": "TEXT",
        "key": "TEXT",
        "request_hash": "TEXT",
        "status": "TEXT",
        "response": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
        "correlation_id": "TEXT",
        "schema_version": "INTEGER",
    },
}
_V2_COLUMNS = {
    **_V1_COLUMNS,
    "schema_migrations": {
        "version": "INTEGER",
        "name": "TEXT",
        "applied_at": "TEXT",
    },
}


class SchemaVersionError(ValueError):
    """The database version cannot be safely interpreted by this build."""


class SchemaValidationError(ValueError):
    """The physical database contract does not match its declared version."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _create_v1(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE paper_accounts (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
        created_at TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL, correlation_id TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '{}')""")
    connection.execute("""CREATE TABLE paper_balances (
        account_id TEXT NOT NULL REFERENCES paper_accounts(id), asset TEXT NOT NULL,
        available TEXT NOT NULL, reserved TEXT NOT NULL, updated_at TEXT NOT NULL,
        created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
        correlation_id TEXT NOT NULL DEFAULT '',
        schema_version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(account_id, asset))""")
    for table in ENTITY_TABLES:
        connection.execute(f"""CREATE TABLE {table} (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES paper_accounts(id),
            execution_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL, correlation_id TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL)""")
        for suffix, column in (
            ("account", "account_id"),
            ("execution", "execution_id"),
            ("correlation", "correlation_id"),
            ("status", "status"),
        ):
            connection.execute(f"CREATE INDEX {table}_{suffix} ON {table}({column})")
    connection.execute("""CREATE TABLE idempotency_records (
        account_id TEXT NOT NULL REFERENCES paper_accounts(id), key TEXT NOT NULL,
        request_hash TEXT NOT NULL, status TEXT NOT NULL, response TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        correlation_id TEXT NOT NULL DEFAULT '',
        schema_version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(account_id,key))""")


def _create_migration_history(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)""")
    applied_at = datetime.now(UTC).isoformat()
    connection.executemany(
        "INSERT INTO schema_migrations(version,name,applied_at) VALUES (?,?,?)",
        (
            (1, "create_paper_ledger", applied_at),
            (2, "add_schema_migration_history", applied_at),
        ),
    )


MIGRATIONS = (
    Migration(1, "create_paper_ledger", _create_v1),
    Migration(2, "add_schema_migration_history", _create_migration_history),
)


def validate_schema(connection: sqlite3.Connection, *, version: int | None = None) -> None:
    """Fail closed when the declared SQLite schema is not the canonical contract."""
    declared = schema_version(connection) if version is None else version
    if declared not in (1, SCHEMA_VERSION):
        raise SchemaVersionError(
            f"Unsupported paper database schema version: {declared}; "
            f"this build supports versions 1 through {SCHEMA_VERSION}"
        )
    expected_columns = _V1_COLUMNS if declared == 1 else _V2_COLUMNS
    expected_tables = V1_TABLES if declared == 1 else V2_TABLES
    actual_tables = _user_tables(connection)
    if actual_tables != expected_tables:
        missing = sorted(expected_tables - actual_tables)
        unexpected = sorted(actual_tables - expected_tables)
        raise SchemaValidationError(
            f"Paper database table contract mismatch; missing={missing}, unexpected={unexpected}"
        )
    for table, expected in expected_columns.items():
        actual = {
            str(row[1]): str(row[2]).upper()
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if actual != expected:
            raise SchemaValidationError(f"Paper database column contract mismatch: {table}")
    for table in V1_TABLES - {"paper_accounts"}:
        references = {
            (str(row[2]), str(row[3]), str(row[4]))
            for row in connection.execute(f"PRAGMA foreign_key_list({table})")
        }
        if ("paper_accounts", "account_id", "id") not in references:
            raise SchemaValidationError(f"Paper database account foreign key missing: {table}")
    if list(connection.execute("PRAGMA foreign_key_check")):
        raise SchemaValidationError("Paper database foreign key check failed")
    for table in ENTITY_TABLES:
        indexes = {
            str(row[1]) for row in connection.execute(f"PRAGMA index_list({table})")
        }
        required = {
            f"{table}_account",
            f"{table}_execution",
            f"{table}_correlation",
            f"{table}_status",
        }
        if not required <= indexes:
            raise SchemaValidationError(f"Paper database index contract mismatch: {table}")
    if declared == SCHEMA_VERSION:
        applied = {
            int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")
        }
        if applied != set(range(1, SCHEMA_VERSION + 1)):
            raise SchemaValidationError("Paper database migration history is incomplete")


def apply_migrations(
    connection: sqlite3.Connection, *, migrations: Sequence[Migration] = MIGRATIONS
) -> int:
    """Apply every pending migration and version update in one transaction."""
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise ValueError("Paper database migrations must be contiguous and ordered")
    current = schema_version(connection)
    target = versions[-1]
    if current > target:
        raise SchemaVersionError(
            f"Paper database schema version {current} is newer than supported version {target}"
        )
    if current == 0 and _user_tables(connection):
        raise SchemaValidationError(
            "Unversioned paper database contains tables; refusing to guess its schema"
        )
    if current:
        validate_schema(connection, version=current)
    if current == target:
        return current
    connection.execute("BEGIN IMMEDIATE")
    try:
        for migration in migrations[current:]:
            migration.apply(connection)
            connection.execute(f"PRAGMA user_version={migration.version}")
        if target <= SCHEMA_VERSION:
            validate_schema(connection, version=target)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return target


__all__ = [
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "Migration",
    "SchemaValidationError",
    "SchemaVersionError",
    "apply_migrations",
    "schema_version",
    "validate_schema",
]
