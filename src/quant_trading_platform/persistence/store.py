"""Durable, transaction-oriented storage; no exchange credentials or execution."""

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 1
_TABLES = {
    "order": "paper_orders", "fill": "paper_fills", "position": "paper_positions",
    "reconciliation": "reconciliation_results", "audit": "audit_events",
}


def decimal_text(value: Decimal) -> str:
    """Canonical fixed-point representation without context-dependent rounding."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Money must be a finite Decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if value == 0 else text


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, float):
        raise ValueError("Floating-point values are not accepted in paper storage")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError(f"Unsupported persistence value: {type(value).__name__}")


def _dump(value: object) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _identifier(value: str, *, maximum: int = 256) -> str:
    if not value or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError("Invalid persistence identifier")
    return value


class SQLitePaperStore:
    """Each transaction owns a connection; a keeper preserves shared memory DBs.

    Pass ``conn=`` to compose CRUD calls inside one transaction. Connections must
    not escape that context. Application callers never need interpolated SQL.
    """

    def __init__(self, path: str | Path) -> None:
        self._keeper: sqlite3.Connection | None = None
        self._closed = False
        self._memory_lock = RLock()
        if str(path) == ":memory:":
            self._target = f"file:paper-{uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
            self._keeper = self._connect()
        else:
            destination = Path(path).expanduser().absolute()
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._target, self._uri = str(destination), False
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("Paper store is closed")
        conn = sqlite3.connect(self._target, uri=self._uri, timeout=10, isolation_level=None,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        # Shared-cache memory SQLite reports SQLITE_LOCKED without busy waiting.
        # Serialize its writers; file databases use SQLite's WAL/busy timeout.
        with self._memory_lock if self._uri else nullcontext():
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def _using(self, conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
        else:
            with self.transaction() as own:
                yield own

    def initialize(self) -> None:
        with self._connect() as config:
            config.execute("PRAGMA journal_mode=WAL")
        config.close()
        with self.transaction() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError(f"Unsupported paper database schema version: {version}")
            conn.execute("""CREATE TABLE IF NOT EXISTS paper_accounts (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL, correlation_id TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '{}')""")
            conn.execute("""CREATE TABLE IF NOT EXISTS paper_balances (
                account_id TEXT NOT NULL REFERENCES paper_accounts(id), asset TEXT NOT NULL,
                available TEXT NOT NULL, reserved TEXT NOT NULL, updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                correlation_id TEXT NOT NULL DEFAULT '',
                schema_version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(account_id, asset))""")
            for table in _TABLES.values():
                # Identifiers come exclusively from the fixed module-level allowlist.
                conn.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES paper_accounts(id),
                    execution_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL, correlation_id TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL)""")
                conn.execute(f"CREATE INDEX IF NOT EXISTS {table}_account ON {table}(account_id)")
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {table}_execution ON {table}(execution_id)")
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {table}_correlation ON {table}(correlation_id)")
                conn.execute(f"CREATE INDEX IF NOT EXISTS {table}_status ON {table}(status)")
            conn.execute("""CREATE TABLE IF NOT EXISTS idempotency_records (
                account_id TEXT NOT NULL REFERENCES paper_accounts(id), key TEXT NOT NULL,
                request_hash TEXT NOT NULL, status TEXT NOT NULL, response TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                correlation_id TEXT NOT NULL DEFAULT '',
                schema_version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(account_id,key))""")
            conn.execute("PRAGMA user_version=1")

    def close(self) -> None:
        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None
        self._closed = True

    def seed_account(
        self, account_id: str = "paper-default", balances: Mapping[str, Decimal] | None = None,
        *, conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        seed = balances if balances is not None else {
            "USDT": Decimal("10000"), "BTC": Decimal("1"), "ETH": Decimal("10"),
        }
        _identifier(account_id)
        with self._using(conn) as db:
            db.execute("""INSERT OR IGNORE INTO paper_accounts
                       (id,name,status,created_at,updated_at,payload) VALUES (?,?,?,?,?,?)""",
                       (account_id, "Paper account", "active", _now(), _now(),
                        _dump({"initial_balances": seed})))
            for asset, amount in seed.items():
                _identifier(asset, maximum=32)
                encoded = decimal_text(amount)
                if amount < 0:
                    raise ValueError("Seed balance cannot be negative")
                db.execute("""INSERT OR IGNORE INTO paper_balances
                    (account_id,asset,available,reserved,updated_at,created_at)
                    VALUES (?,?,?,?,?,?)""", (account_id, asset, encoded, "0", _now(), _now()))
            result = self.get_account(account_id, conn=db)
            assert result is not None
            return result

    def get_account(self, account_id: str, *, conn: sqlite3.Connection | None = None
                    ) -> dict[str, Any] | None:
        _identifier(account_id)
        with self._using(conn) as db:
            row = db.execute("SELECT * FROM paper_accounts WHERE id=?", (account_id,)).fetchone()
            if row is None:
                return None
            result = dict(json.loads(row["payload"]))
            result.update({key: value for key, value in dict(row).items() if key != "payload"})
            return result

    def upsert_account(self, account_id: str, record: Mapping[str, object], *,
                       conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        _identifier(account_id)
        with self._using(conn) as db:
            existing = self.get_account(account_id, conn=db) or {}
            value = {**existing, **record, "id": account_id}
            db.execute("""INSERT INTO paper_accounts
                (id,name,status,created_at,updated_at,correlation_id,payload) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,status=excluded.status,
                updated_at=excluded.updated_at,correlation_id=excluded.correlation_id,
                payload=excluded.payload""", (
                account_id, str(value.get("name", "Paper account")),
                str(value.get("status", "active")), str(value.get("created_at", _now())),
                _now(), str(value.get("correlation_id", "")), _dump(value)))
            result = self.get_account(account_id, conn=db)
            assert result is not None
            return result

    def list_accounts(self, *, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        with self._using(conn) as db:
            ids = [row[0] for row in db.execute("SELECT id FROM paper_accounts ORDER BY id")]
            return [result for identifier in ids
                    if (result := self.get_account(identifier, conn=db)) is not None]

    def get_balance(self, account_id: str, asset: str, *, conn: sqlite3.Connection | None = None
                    ) -> dict[str, Any] | None:
        _identifier(account_id)
        _identifier(asset, maximum=32)
        with self._using(conn) as db:
            row = db.execute("SELECT * FROM paper_balances WHERE account_id=? AND asset=?",
                             (account_id, asset)).fetchone()
            return None if row is None else dict(row)

    def list_balances(self, account_id: str, *, conn: sqlite3.Connection | None = None
                      ) -> list[dict[str, Any]]:
        _identifier(account_id)
        with self._using(conn) as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM paper_balances WHERE account_id=? ORDER BY asset", (account_id,))]

    def upsert_balance(self, account_id: str, asset: str, available: Decimal,
                       reserved: Decimal = Decimal("0"), *,
                       conn: sqlite3.Connection | None = None) -> None:
        available_text, reserved_text = decimal_text(available), decimal_text(reserved)
        _identifier(account_id)
        _identifier(asset, maximum=32)
        if available < 0 or reserved < 0:
            raise ValueError("Paper balances cannot be negative")
        with self._using(conn) as db:
            db.execute("""INSERT INTO paper_balances
                (account_id,asset,available,reserved,updated_at,created_at) VALUES (?,?,?,?,?,?)
                ON CONFLICT(account_id,asset) DO UPDATE SET available=excluded.available,
                reserved=excluded.reserved,updated_at=excluded.updated_at""",
                       (account_id, asset, available_text, reserved_text, _now(), _now()))

    def _insert(self, entity: str, record: Mapping[str, object],
                conn: sqlite3.Connection | None) -> dict[str, Any]:
        table = _TABLES[entity]
        value = dict(record)
        identifier = str(value.get("id") or value.get(f"{entity}_id") or uuid4())
        account_id = str(value.get("account_id") or "paper-default")
        _identifier(identifier)
        _identifier(account_id)
        if "id" in value:
            _identifier(str(value["id"]))
        if "account_id" in value:
            _identifier(str(value["account_id"]))
        for field in ("execution_id", "correlation_id"):
            if value.get(field):
                _identifier(str(value[field]))
        value.update(id=identifier, account_id=account_id,
                     execution_id=str(value.get("execution_id") or ""),
                     status=str(value.get("status") or ""),
                     created_at=str(value.get("created_at") or _now()), schema_version=1,
                     updated_at=str(value.get("updated_at") or _now()),
                     correlation_id=str(value.get("correlation_id") or ""))
        payload = _dump(value)
        with self._using(conn) as db:
            db.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?)", (
                identifier, account_id, value["execution_id"], value["status"],
                value["created_at"], 1, value["updated_at"], value["correlation_id"], payload))
        return dict(json.loads(payload))

    def _get(self, entity: str, identifier: str, conn: sqlite3.Connection | None
             ) -> dict[str, Any] | None:
        _identifier(identifier)
        with self._using(conn) as db:
            row = db.execute(f"SELECT payload FROM {_TABLES[entity]} WHERE id=?",
                             (identifier,)).fetchone()
            return None if row is None else dict(json.loads(row[0]))

    def _list(self, entity: str, account_id: str | None, conn: sqlite3.Connection | None
              ) -> list[dict[str, Any]]:
        if account_id is not None:
            _identifier(account_id)
        with self._using(conn) as db:
            query = f"SELECT payload FROM {_TABLES[entity]}"
            args: tuple[str, ...] = ()
            if account_id is not None:
                query += " WHERE account_id=?"
                args = (account_id,)
            return [dict(json.loads(row[0]))
                    for row in db.execute(query + " ORDER BY rowid DESC", args)]

    def get_idempotency(self, account_id: str, key: str, *,
                        conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        _identifier(account_id)
        _identifier(key, maximum=128)
        with self._using(conn) as db:
            row = db.execute("SELECT * FROM idempotency_records WHERE account_id=? AND key=?",
                             (account_id, key)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["response"] = None if row["response"] is None else json.loads(row["response"])
            return result

    def reserve_idempotency(self, account_id: str, key: str, request_hash: str, *,
                            conn: sqlite3.Connection | None = None) -> bool:
        _identifier(account_id)
        _identifier(key, maximum=128)
        _identifier(request_hash)
        with self._using(conn) as db:
            cursor = db.execute("""INSERT INTO idempotency_records
                (account_id,key,request_hash,status,response,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(account_id,key) DO NOTHING""",
                (account_id, key, request_hash, "pending", None, _now(), _now()))
            return cursor.rowcount == 1

    def complete_idempotency(self, account_id: str, key: str, response: Mapping[str, object], *,
                             conn: sqlite3.Connection | None = None) -> None:
        _identifier(account_id)
        _identifier(key, maximum=128)
        with self._using(conn) as db:
            cursor = db.execute("""UPDATE idempotency_records SET status='completed',response=?,
                updated_at=? WHERE account_id=? AND key=? AND status='pending'""",
                (_dump(response), _now(), account_id, key))
            if cursor.rowcount != 1:
                raise ValueError("Idempotency reservation missing or already completed")

    def insert_order(self, record: Mapping[str, object], *, conn: sqlite3.Connection | None = None
                     ) -> dict[str, Any]:
        return self._insert("order", record, conn)

    def insert_fill(self, record: Mapping[str, object], *, conn: sqlite3.Connection | None = None
                    ) -> dict[str, Any]:
        return self._insert("fill", record, conn)

    def insert_position(self, record: Mapping[str, object], *,
                        conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        return self._insert("position", record, conn)

    def insert_reconciliation(self, record: Mapping[str, object], *,
                              conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        return self._insert("reconciliation", record, conn)

    def insert_audit(self, record: Mapping[str, object], *, conn: sqlite3.Connection | None = None
                     ) -> dict[str, Any]:
        return self._insert("audit", record, conn)

    def get_order(self, identifier: str, *, conn: sqlite3.Connection | None = None
                  ) -> dict[str, Any] | None:
        return self._get("order", identifier, conn)

    def get_fill(self, identifier: str, *, conn: sqlite3.Connection | None = None
                 ) -> dict[str, Any] | None:
        return self._get("fill", identifier, conn)

    def get_position(self, identifier: str, *, conn: sqlite3.Connection | None = None
                     ) -> dict[str, Any] | None:
        return self._get("position", identifier, conn)

    def get_reconciliation(self, identifier: str, *, conn: sqlite3.Connection | None = None
                           ) -> dict[str, Any] | None:
        return self._get("reconciliation", identifier, conn)

    def get_audit(self, identifier: str, *, conn: sqlite3.Connection | None = None
                  ) -> dict[str, Any] | None:
        return self._get("audit", identifier, conn)

    def list_orders(self, account_id: str | None = None, *, conn: sqlite3.Connection | None = None
                    ) -> list[dict[str, Any]]:
        return self._list("order", account_id, conn)

    def list_fills(self, account_id: str | None = None, *, conn: sqlite3.Connection | None = None
                   ) -> list[dict[str, Any]]:
        return self._list("fill", account_id, conn)

    def list_positions(self, account_id: str | None = None, *,
                       conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        return self._list("position", account_id, conn)

    def list_reconciliations(self, account_id: str | None = None, *,
                             conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        return self._list("reconciliation", account_id, conn)

    def _update_entity(self, entity: str, record: Mapping[str, object],
                       conn: sqlite3.Connection | None, *, upsert: bool) -> dict[str, Any]:
        identifier = str(record.get("id") or record.get(f"{entity}_id") or "")
        if not identifier:
            raise ValueError("An existing stable ID is required")
        with self._using(conn) as db:
            previous = self._get(entity, identifier, db)
            if previous is None:
                if upsert:
                    return self._insert(entity, record, db)
                raise KeyError(identifier)
            value = {**previous, **record, "id": identifier}
            if value["account_id"] != previous["account_id"]:
                raise ValueError("Entity account ownership cannot change")
            value["created_at"] = previous["created_at"]
            value["schema_version"] = SCHEMA_VERSION
            value["updated_at"] = _now()
            payload = _dump(value)
            db.execute(f"""UPDATE {_TABLES[entity]} SET execution_id=?,status=?,payload=?,
                       updated_at=?,correlation_id=? WHERE id=?""",
                       (value["execution_id"], value["status"], payload,
                        value["updated_at"], value["correlation_id"], identifier))
            return dict(json.loads(payload))

    def update_order(self, record: Mapping[str, object], *,
                     conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        return self._update_entity("order", record, conn, upsert=False)

    def upsert_position(self, record: Mapping[str, object], *,
                        conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        return self._update_entity("position", record, conn, upsert=True)

    @staticmethod
    def _audit_filters(account_id: str | None, event_type: str | None, order_id: str | None,
                       reason_code: str | None, correlation_id: str | None
                       ) -> tuple[str, list[str]]:
        clauses: list[str] = []
        args: list[str] = []
        for expression, value in (
            ("account_id", account_id),
            ("COALESCE(json_extract(payload,'$.event_type'),"
             "json_extract(payload,'$.event'),json_extract(payload,'$.action'))", event_type),
            ("json_extract(payload,'$.order_id')", order_id),
            ("json_extract(payload,'$.reason_code')", reason_code),
            ("correlation_id", correlation_id),
        ):
            if value is not None:
                clauses.append(expression + "=?")
                args.append(value)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), args

    def list_audit(self, account_id: str | None = None, *, limit: int = 100, offset: int = 0,
                   event_type: str | None = None, order_id: str | None = None,
                   reason_code: str | None = None, correlation_id: str | None = None,
                   conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        if not 1 <= limit <= 10_000 or offset < 0:
            raise ValueError("Invalid audit pagination")
        clause, args = self._audit_filters(account_id, event_type, order_id,
                                           reason_code, correlation_id)
        with self._using(conn) as db:
            rows = db.execute("SELECT payload FROM audit_events" + clause +
                              " ORDER BY rowid DESC LIMIT ? OFFSET ?", (*args, limit, offset))
            return [dict(json.loads(row[0])) for row in rows]

    def count_audit(self, account_id: str | None = None, *, event_type: str | None = None,
                    order_id: str | None = None, reason_code: str | None = None,
                    correlation_id: str | None = None,
                    conn: sqlite3.Connection | None = None) -> int:
        clause, args = self._audit_filters(account_id, event_type, order_id,
                                           reason_code, correlation_id)
        with self._using(conn) as db:
            return int(db.execute("SELECT COUNT(*) FROM audit_events" + clause, args).fetchone()[0])
