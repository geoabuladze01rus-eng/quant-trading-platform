from decimal import Decimal

import pytest

from quant_trading_platform.audit_log import AuditLog, PersistentAuditLog
from quant_trading_platform.audit_log.persistent import advanced_decision


class Store:
    def __init__(self):
        self.rows = []

    def insert_audit(self, record):
        self.rows.append(record)

    def list_audit(self, *, limit=100, offset=0, **filters):
        rows = [row for row in reversed(self.rows)
                if all(value is None or row.get(key) == value for key, value in filters.items())]
        return rows[offset:offset + limit]

    def count_audit(self, **filters):
        return len(self.list_audit(limit=500, **filters))


def test_adapter_restart_detachment_pagination_and_exact_decimals():
    store = Store()
    audit = PersistentAuditLog(store)
    assert audit.list() == []
    first = audit.record("risk_decision", "insufficient_net_edge", order_id="one",
                         correlation_id="request-one", fees=Decimal("0.100000000000000001"))
    first["fees"] = "wrong"
    restarted = PersistentAuditLog(store)
    rows = restarted.list(order_id="one", correlation_id="request-one")
    assert rows[0]["fees"] == "0.100000000000000001"
    rows[0]["decision"] = "tampered"
    assert restarted.list()[0]["decision"] == ""
    assert restarted.count(reason_code="insufficient_net_edge") == 1
    assert restarted.list(offset=1) == []
    assert restarted.list(event_type="other") == []


@pytest.mark.parametrize("fields", [{"api_key": "secret"}, {"payload": {}},
                                   {"fees": 0.1}, {"fees": Decimal("NaN")},
                                   {"data_age_ms": -1}])
def test_reject_unsafe_audit_fields(fields):
    store = Store()
    with pytest.raises(ValueError):
        PersistentAuditLog(store).record("risk", "invalid_order", **fields)
    assert store.rows == []


@pytest.mark.parametrize("limit,offset", [(501, 0), (0, 0), (10, -1), (True, 0)])
def test_bounded_reads(limit, offset):
    with pytest.raises(ValueError):
        PersistentAuditLog(Store()).list(limit, offset)


def test_advanced_dto_hides_undocumented_fields():
    result = advanced_decision({"reason_code": "paper_order_rejected", "api_key": "secret",
                                "fees": Decimal("0.0100")})
    assert "api_key" not in result
    assert result["fees"] == "0.0100"
    assert "отклонена" in result["human_reason"]
    assert AuditLog().list() == []
