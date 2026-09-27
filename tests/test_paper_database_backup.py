import sqlite3
import stat
from decimal import Decimal
from pathlib import Path

import pytest

from quant_trading_platform.persistence import backup as backup_module
from quant_trading_platform.persistence.backup import (
    create_backup,
    restore_backup,
    verify_database,
)
from quant_trading_platform.persistence.migrations import SCHEMA_VERSION, SchemaVersionError
from quant_trading_platform.persistence.store import SQLitePaperStore


def make_database(path):
    store = SQLitePaperStore(path)
    store.seed_account()
    store.close()


def test_backup_is_consistent_private_and_verifiable(tmp_path):
    database = tmp_path / "paper.db"
    backup = tmp_path / "backups" / "paper-copy.db"
    make_database(database)

    result = create_backup(database, backup)

    assert result == backup
    assert verify_database(backup)["integrity"] == "ok"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    connection = sqlite3.connect(backup)
    try:
        assert connection.execute("SELECT COUNT(*) FROM paper_accounts").fetchone()[0] == 1
    finally:
        connection.close()


def test_backup_uses_wal_snapshot_without_changing_source(tmp_path):
    database = tmp_path / "paper.db"
    backup = tmp_path / "paper-copy.db"
    store = SQLitePaperStore(database)
    store.seed_account()
    store.upsert_balance("paper-default", "USDT", Decimal("1234"), Decimal("0"))

    create_backup(database, backup)

    connection = sqlite3.connect(backup)
    try:
        row = connection.execute(
            """SELECT available FROM paper_balances
            WHERE account_id = 'paper-default' AND asset = 'USDT'"""
        ).fetchone()
        assert row[0] == "1234"
    finally:
        connection.close()
        store.close()
    assert verify_database(database)["integrity"] == "ok"


def test_backup_never_overwrites_an_existing_file(tmp_path):
    database = tmp_path / "paper.db"
    backup = tmp_path / "paper-copy.db"
    make_database(database)
    backup.write_text("keep this file")

    with pytest.raises(FileExistsError):
        create_backup(database, backup)

    assert backup.read_text() == "keep this file"


def test_verification_rejects_corrupt_or_unrelated_files(tmp_path):
    unrelated = tmp_path / "not-a-database.db"
    destination = tmp_path / "should-not-exist.db"
    unrelated.write_text("not sqlite")

    with pytest.raises((sqlite3.DatabaseError, ValueError)):
        verify_database(unrelated)
    with pytest.raises((sqlite3.DatabaseError, ValueError)):
        create_backup(unrelated, destination)
    assert not destination.exists()


def test_backup_destination_cannot_be_source(tmp_path):
    database = tmp_path / "paper.db"
    make_database(database)

    with pytest.raises(ValueError, match="differ"):
        create_backup(database, database)


def balance(path, asset="USDT"):
    store = SQLitePaperStore(path)
    try:
        record = store.get_balance("paper-default", asset)
        assert record is not None
        return record["available"]
    finally:
        store.close()


def test_restore_requires_confirmation_then_preserves_recovery_copy(tmp_path):
    source = tmp_path / "source.db"
    restore_point = tmp_path / "restore-point.db"
    target = tmp_path / "active.db"
    recovery = tmp_path / "recovery" / "before-restore.db"
    source_store = SQLitePaperStore(source)
    source_store.seed_account()
    source_store.upsert_balance(
        "paper-default", "USDT", Decimal("123456789.123456789123456789")
    )
    source_store.close()
    create_backup(source, restore_point)
    target_store = SQLitePaperStore(target)
    target_store.seed_account()
    target_store.upsert_balance("paper-default", "USDT", Decimal("42"))
    target_store.close()

    with pytest.raises(FileExistsError, match="explicit confirmation"):
        restore_backup(restore_point, target)
    assert balance(target) == "42"

    result = restore_backup(
        restore_point,
        target,
        confirm_replace=True,
        recovery_backup=recovery,
    )

    assert result["schema_version"] == SCHEMA_VERSION
    assert result["live_trading"] == "locked"
    assert balance(target) == "123456789.123456789123456789"
    assert balance(recovery) == "42"
    assert verify_database(recovery, require_standalone=True)["integrity"] == "ok"


@pytest.mark.parametrize("kind", ["corrupt", "future"])
def test_incompatible_restore_source_leaves_original_database_intact(tmp_path, kind):
    target = tmp_path / "active.db"
    source = tmp_path / "source.db"
    recovery = tmp_path / "recovery.db"
    target_store = SQLitePaperStore(target)
    target_store.seed_account()
    target_store.upsert_balance("paper-default", "USDT", Decimal("77.123456789"))
    target_store.close()
    if kind == "corrupt":
        source.write_bytes(b"not a SQLite database")
        expected_error = (sqlite3.DatabaseError, ValueError)
    else:
        make_database(source)
        connection = sqlite3.connect(source)
        connection.execute("PRAGMA user_version=999")
        connection.commit()
        connection.close()
        expected_error = SchemaVersionError

    with pytest.raises(expected_error):
        restore_backup(
            source,
            target,
            confirm_replace=True,
            recovery_backup=recovery,
        )

    assert balance(target) == "77.123456789"
    assert not recovery.exists()


def test_failed_atomic_replace_leaves_original_and_verified_recovery(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    restore_point = tmp_path / "restore.db"
    target = tmp_path / "active.db"
    recovery = tmp_path / "recovery.db"
    make_database(source)
    create_backup(source, restore_point)
    target_store = SQLitePaperStore(target)
    target_store.seed_account()
    target_store.upsert_balance("paper-default", "USDT", Decimal("88.000000000000000001"))
    target_store.close()

    def fail_replace(_source, _destination):
        raise OSError("injected replace failure")

    monkeypatch.setattr(backup_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        restore_backup(
            restore_point,
            target,
            confirm_replace=True,
            recovery_backup=recovery,
        )

    assert balance(target) == "88.000000000000000001"
    assert balance(recovery) == "88.000000000000000001"


def test_restore_refuses_busy_database_and_dependent_wal_artifact(tmp_path):
    source = tmp_path / "source.db"
    restore_point = tmp_path / "restore.db"
    target = tmp_path / "active.db"
    recovery = tmp_path / "recovery.db"
    make_database(source)
    create_backup(source, restore_point)
    make_database(target)
    connection = sqlite3.connect(target, isolation_level=None)
    connection.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(RuntimeError, match="stop the API"):
            restore_backup(
                restore_point,
                target,
                confirm_replace=True,
                recovery_backup=recovery,
            )
    finally:
        connection.rollback()
        connection.close()
    assert verify_database(target)["integrity"] == "ok"
    assert not recovery.exists()

    Path(f"{restore_point}-wal").write_bytes(b"sidecar")
    with pytest.raises(ValueError, match="sidecar"):
        restore_backup(restore_point, tmp_path / "new.db")
