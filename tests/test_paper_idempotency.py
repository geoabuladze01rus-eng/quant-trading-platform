from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from test_paper_accounting import execute, service

from quant_trading_platform.paper_trading.models import PaperCommandError, canonical_hash
from quant_trading_platform.paper_trading.service import PersistentPaperService
from quant_trading_platform.persistence.store import SQLitePaperStore


def test_restart_replays_original_response_not_current_market(tmp_path):
    path = tmp_path / "paper.db"
    first = service(path)
    expected = execute(first)
    first.store.close()
    second = PersistentPaperService(SQLitePaperStore(path))
    assert execute(second, depth=".01") == expected
    assert len(second.store.list_orders()) == 1
    assert len(second.store.list_fills()) == 2


def test_same_key_conflicting_payload_is_rejected(tmp_path):
    instance = service(tmp_path / "paper.db")
    execute(instance)
    with pytest.raises(PaperCommandError) as error:
        execute(instance, amount="99")
    assert error.value.reason_code == "duplicate_idempotency_key"
    assert len(instance.store.list_orders()) == 1


def test_concurrent_same_key_exactly_once_across_service_instances(tmp_path):
    path = tmp_path / "paper.db"
    original = service(path)
    services = [PersistentPaperService(SQLitePaperStore(path)) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(execute, services))
    assert all(result == responses[0] for result in responses)
    assert len(original.store.list_orders()) == 1
    assert len(original.store.list_fills()) == 2
    assert original.reconcile()["issues"] == []


def test_concurrent_distinct_commands_cannot_overspend(tmp_path):
    path = tmp_path / "paper.db"
    original = service(path, usdt="110", btc="1")
    services = [PersistentPaperService(SQLitePaperStore(path)) for _ in range(3)]
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(
            executor.map(
                lambda item: execute(item[1], key=str(item[0]), depth=".5"), enumerate(services)
            )
        )
    assert sum(result["status"] == "partially_filled" for result in results) == 1
    assert all(
        Decimal(row["available"]) >= 0 and Decimal(row["reserved"]) >= 0
        for row in original.account()["balances"]
    )


def test_cancel_replay_and_operation_collision(tmp_path):
    instance = service(tmp_path / "paper.db")
    order = execute(instance, depth=".5")
    cancelled = instance.cancel(order["order_id"], idempotency_key="cancel")
    assert instance.cancel(order["order_id"], idempotency_key="cancel") == cancelled
    with pytest.raises(PaperCommandError) as error:
        instance.cancel(order["order_id"], idempotency_key="first")
    assert error.value.reason_code == "duplicate_idempotency_key"


def test_canonical_hash_order_and_decimal_equivalence(tmp_path):
    assert canonical_hash({"a": 1, "b": "2"}) == canonical_hash({"b": "2", "a": 1})
    instance = service(tmp_path / "paper.db")
    assert execute(instance, amount="100.00") == execute(instance, amount="100")
