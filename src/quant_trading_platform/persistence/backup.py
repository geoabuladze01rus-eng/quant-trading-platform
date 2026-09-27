"""Verified backup and explicit offline restore for the local paper ledger."""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

from quant_trading_platform.persistence.migrations import (
    apply_migrations,
    schema_version,
    validate_schema,
)

_DEFAULT_DATABASE = Path(os.environ.get("PAPER_DATABASE_PATH", "data/paper_alpha.sqlite3"))


def _sidecars(path: Path) -> tuple[Path, Path]:
    return Path(f"{path}-wal"), Path(f"{path}-shm")


def _readonly_connection(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve(strict=True)
    uri = f"file:{quote(str(resolved), safe='/:')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_database(
    path: str | Path, *, require_standalone: bool = False
) -> dict[str, object]:
    """Verify integrity, foreign keys and the declared canonical schema."""
    database = Path(path).expanduser().resolve(strict=True)
    if require_standalone:
        present = [str(sidecar) for sidecar in _sidecars(database) if sidecar.exists()]
        if present:
            raise ValueError("Restore artifact must not depend on WAL or SHM sidecar files")
    connection = _readonly_connection(database)
    try:
        checks = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if checks != ["ok"]:
            raise ValueError("SQLite integrity_check failed")
        validate_schema(connection)
        version = schema_version(connection)
        tables = sorted(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        return {"integrity": "ok", "schema_version": version, "tables": tables}
    finally:
        connection.close()


def _publish_snapshot(temporary_path: Path, destination: Path) -> None:
    os.chmod(temporary_path, 0o600)
    _fsync_file(temporary_path)
    # Publishing with a hard link is atomic and refuses a racing overwrite.
    os.link(temporary_path, destination)
    _fsync_directory(destination.parent)


def create_backup(database: str | Path, destination: str | Path) -> Path:
    """Create a verified, durable, owner-only, non-overwriting SQLite snapshot."""
    source_path = Path(database).expanduser().resolve(strict=True)
    backup_path = Path(destination).expanduser().absolute()
    if source_path == backup_path.resolve():
        raise ValueError("Backup destination must differ from the source database")
    if backup_path.exists():
        raise FileExistsError(f"Backup already exists: {backup_path}")
    verify_database(source_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{backup_path.name}.", suffix=".tmp", dir=backup_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source = _readonly_connection(source_path)
        target = sqlite3.connect(temporary_path, timeout=10)
        source.backup(target, pages=128, sleep=0.01)
        target.execute("PRAGMA journal_mode=DELETE")
        target.close()
        target = None
        verify_database(temporary_path, require_standalone=True)
        _publish_snapshot(temporary_path, backup_path)
        return backup_path
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        temporary_path.unlink(missing_ok=True)


def _stage_restore(source_path: Path, destination: Path) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.restore.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    staged = Path(temporary_name)
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source = _readonly_connection(source_path)
        target = sqlite3.connect(staged, timeout=10, isolation_level=None)
        target.execute("PRAGMA foreign_keys=ON")
        source.backup(target, pages=128, sleep=0.01)
        apply_migrations(target)
        target.execute("PRAGMA journal_mode=DELETE")
        target.close()
        target = None
        source.close()
        source = None
        verify_database(staged, require_standalone=True)
        os.chmod(staged, 0o600)
        _fsync_file(staged)
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()


def _require_idle_and_checkpoint(path: Path) -> None:
    connection = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout=0")
        connection.execute("BEGIN EXCLUSIVE")
        connection.rollback()
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is not None and int(checkpoint[0]) != 0:
            raise RuntimeError("Paper database is busy; stop the API before restore")
    except sqlite3.OperationalError as error:
        raise RuntimeError("Paper database is busy; stop the API before restore") from error
    finally:
        connection.close()


def restore_backup(
    backup: str | Path,
    database: str | Path,
    *,
    confirm_replace: bool = False,
    recovery_backup: str | Path | None = None,
) -> dict[str, object]:
    """Restore a verified snapshot through a staged, atomic offline replacement."""
    backup_path = Path(backup).expanduser().resolve(strict=True)
    database_path = Path(database).expanduser().absolute()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path == database_path.resolve():
        raise ValueError("Restore source must differ from the destination database")
    verify_database(backup_path, require_standalone=True)
    exists = database_path.exists()
    recovery_path: Path | None = None
    if exists:
        if not confirm_replace:
            raise FileExistsError(
                "Destination database exists; pass explicit confirmation and a recovery backup path"
            )
        if recovery_backup is None:
            raise ValueError("A non-existing recovery backup path is required before replacement")
        recovery_path = Path(recovery_backup).expanduser().absolute()
        if recovery_path.exists():
            raise FileExistsError(f"Recovery backup already exists: {recovery_path}")
        if recovery_path.resolve() in (backup_path, database_path.resolve()):
            raise ValueError("Recovery backup path must be distinct")

    staged = _stage_restore(backup_path, database_path)
    replaced = False
    try:
        if exists:
            _require_idle_and_checkpoint(database_path)
            assert recovery_path is not None
            create_backup(database_path, recovery_path)
            verify_database(recovery_path, require_standalone=True)
            for sidecar in _sidecars(database_path):
                sidecar.unlink(missing_ok=True)
        os.replace(staged, database_path)
        replaced = True
        _fsync_directory(database_path.parent)
        result = verify_database(database_path, require_standalone=True)
    except BaseException:
        if replaced and recovery_path is not None and recovery_path.exists():
            rollback = _stage_restore(recovery_path, database_path)
            try:
                os.replace(rollback, database_path)
                _fsync_directory(database_path.parent)
                verify_database(database_path, require_standalone=True)
            finally:
                rollback.unlink(missing_ok=True)
        raise
    finally:
        staged.unlink(missing_ok=True)
    return {
        "database": str(database_path),
        "integrity": result["integrity"],
        "schema_version": result["schema_version"],
        "recovery_backup": None if recovery_path is None else str(recovery_path),
        "live_trading": "locked",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Back up, verify or explicitly restore the local paper SQLite database."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="create a verified snapshot without overwriting")
    backup.add_argument("--database", type=Path, default=_DEFAULT_DATABASE)
    backup.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="check integrity and canonical ledger schema")
    verify.add_argument("database", type=Path)
    verify.add_argument(
        "--standalone",
        action="store_true",
        help="also reject a snapshot that has adjacent WAL/SHM files",
    )
    restore = commands.add_parser("restore", help="offline restore with an atomic replacement")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--database", type=Path, default=_DEFAULT_DATABASE)
    restore.add_argument("--recovery-backup", type=Path)
    restore.add_argument("--confirm-replace", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "backup":
            path = create_backup(args.database, args.output)
            result = verify_database(path, require_standalone=True)
            print(
                f"Backup verified: {path} "
                f"(schema {result['schema_version']}, integrity {result['integrity']})"
            )
        elif args.command == "restore":
            result = restore_backup(
                args.backup,
                args.database,
                confirm_replace=args.confirm_replace,
                recovery_backup=args.recovery_backup,
            )
            print(
                f"Paper database restored: {result['database']} "
                f"(schema {result['schema_version']}, live trading locked)"
            )
        else:
            result = verify_database(args.database, require_standalone=args.standalone)
            print(
                f"Database verified: {args.database} "
                f"(schema {result['schema_version']}, integrity {result['integrity']})"
            )
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
        print(f"Paper database operation failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
