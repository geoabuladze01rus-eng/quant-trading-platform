import sqlite3
import stat
from decimal import Decimal

import pytest

from quant_trading_platform.persistence.backup import create_backup, verify_database
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
    unrelated.write_text("not sqlite")

    with pytest.raises((sqlite3.DatabaseError, ValueError)):
        verify_database(unrelated)


def test_backup_destination_cannot_be_source(tmp_path):
    database = tmp_path / "paper.db"
    make_database(database)

    with pytest.raises(ValueError, match="differ"):
        create_backup(database, database)
