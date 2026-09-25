"""Safe SQLite backup and verification helpers for the local paper account.

Backups use SQLite's online backup API so a consistent snapshot includes WAL
content. This module never changes the source database or silently overwrites a
previous backup.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

_REQUIRED_TABLES = frozenset({
    "paper_accounts",
    "paper_balances",
    "paper_orders",
    "paper_fills",
    "paper_positions",
    "reconciliation_results",
    "audit_events",
    "idempotency_records",
})
_DEFAULT_DATABASE = Path(os.environ.get("PAPER_DATABASE_PATH", "data/paper_alpha.sqlite3"))


def _readonly_connection(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve(strict=True)
    uri = f"file:{quote(str(resolved), safe='/:')}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


def verify_database(path: str | Path) -> dict[str, object]:
    """Verify SQLite integrity and the expected paper ledger tables."""
    database = Path(path).expanduser()
    connection = _readonly_connection(database)
    try:
        checks = [row[0] for row in connection.execute("PRAGMA quick_check")]
        if checks != ["ok"]:
            raise ValueError("SQLite quick_check failed")
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if schema_version < 1:
            raise ValueError("Database schema version is missing or unsupported")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = sorted(_REQUIRED_TABLES - tables)
        if missing:
            raise ValueError(f"Paper database is missing required tables: {', '.join(missing)}")
        return {
            "integrity": "ok",
            "schema_version": schema_version,
            "tables": sorted(tables),
        }
    finally:
        connection.close()


def create_backup(database: str | Path, destination: str | Path) -> Path:
    """Create a verified, owner-only, non-overwriting SQLite snapshot."""
    source_path = Path(database).expanduser().resolve(strict=True)
    backup_path = Path(destination).expanduser().absolute()
    if source_path == backup_path.resolve():
        raise ValueError("Backup destination must differ from the source database")
    if backup_path.exists():
        raise FileExistsError(f"Backup already exists: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{backup_path.name}.", suffix=".tmp", dir=backup_path.parent
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source = _readonly_connection(source_path)
        target = sqlite3.connect(temporary_path, timeout=10)
        source.backup(target, pages=128, sleep=0.01)
        target.close()
        target = None
        verify_database(temporary_path)
        os.chmod(temporary_path, 0o600)
        # A hard link publishes atomically and fails if another process created
        # the destination after the initial existence check.
        os.link(temporary_path, backup_path)
        return backup_path
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        temporary_path.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Back up or verify the local paper SQLite database.")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="create a verified snapshot without overwriting")
    backup.add_argument("--database", type=Path, default=_DEFAULT_DATABASE)
    backup.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="check integrity and required ledger tables")
    verify.add_argument("database", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "backup":
            path = create_backup(args.database, args.output)
            result = verify_database(path)
            print(
                f"Backup verified: {path} "
                f"(schema {result['schema_version']}, integrity {result['integrity']})"
            )
        else:
            result = verify_database(args.database)
            print(
                f"Database verified: {args.database} "
                f"(schema {result['schema_version']}, integrity {result['integrity']})"
            )
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"Paper database operation failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
