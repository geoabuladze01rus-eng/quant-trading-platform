"""Persistent, detached audit views with exact decimal serialization."""

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import uuid4

from quant_trading_platform.explainability.reasons import human_reason


class AuditStore(Protocol):
    def insert_audit(self, record: dict[str, object]) -> object: ...

    def list_audit(
        self, *, limit: int, offset: int, event_type: str | None,
        order_id: str | None, reason_code: str | None, correlation_id: str | None,
    ) -> list[dict[str, object]]: ...

    def count_audit(
        self, *, event_type: str | None, order_id: str | None,
        reason_code: str | None, correlation_id: str | None,
    ) -> int: ...


_FIELDS = frozenset((
    "event_id", "timestamp", "actor", "actor_type", "event_type", "strategy", "symbol",
    "venue", "market_type", "order_id", "opportunity_id", "decision", "reason_code",
    "human_reason", "risk_score", "gross_edge", "fees", "slippage", "net_edge",
    "data_age_ms", "correlation_id", "algorithm_version",
))
_FINANCIAL = frozenset(("gross_edge", "fees", "slippage", "net_edge"))


class PersistentAuditLog:
    def __init__(self, store: AuditStore) -> None:
        self.store = store

    def record(self, event_type: str, reason_code: str, **fields: object) -> dict[str, object]:
        """Allowlisted fields prevent accidental credential/payload persistence."""
        if set(fields) - _FIELDS:
            raise ValueError("Unsupported audit fields; secrets and raw payloads are prohibited")
        item: dict[str, object] = dict.fromkeys(_FIELDS, "")
        item.update({
            "event_id": str(uuid4()), "timestamp": datetime.now(UTC).isoformat(),
            "actor": "system", "actor_type": "system", "algorithm_version": "1",
            "risk_score": "blocked", "data_age_ms": None,
            **dict.fromkeys(_FINANCIAL, None), **fields,
            "event_type": event_type, "reason_code": reason_code,
            "human_reason": human_reason(reason_code),
        })
        for key, value in item.items():
            if key in _FINANCIAL:
                if value is None:
                    continue
                if not isinstance(value, (Decimal, str)):
                    raise ValueError("Financial audit values require Decimal or exact decimal text")
                decimal = Decimal(value)
                if not decimal.is_finite():
                    raise ValueError("Nonfinite audit value")
                item[key] = str(decimal)
            elif key == "data_age_ms":
                if value is not None and (type(value) is not int or value < 0):
                    raise ValueError("Invalid audit data age")
            elif not isinstance(value, str):
                raise ValueError("Audit text fields must be strings")
        self.store.insert_audit(deepcopy(item))
        return deepcopy(item)

    def list(
        self, limit: int = 100, offset: int = 0, *, event_type: str | None = None,
        order_id: str | None = None, reason_code: str | None = None,
        correlation_id: str | None = None,
    ) -> list[dict[str, object]]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Audit limit must be between 1 and 500")
        if type(offset) is not int or offset < 0:
            raise ValueError("Audit offset must be nonnegative")
        return deepcopy(self.store.list_audit(
            limit=limit, offset=offset, event_type=event_type, order_id=order_id,
            reason_code=reason_code, correlation_id=correlation_id,
        ))

    def count(
        self, *, event_type: str | None = None, order_id: str | None = None,
        reason_code: str | None = None, correlation_id: str | None = None,
    ) -> int:
        return self.store.count_audit(
            event_type=event_type, order_id=order_id, reason_code=reason_code,
            correlation_id=correlation_id,
        )


def advanced_decision(event: Mapping[str, object]) -> dict[str, object]:
    """Read-only decision DTO; expose only the documented audit fields."""
    result = {key: deepcopy(value) for key, value in event.items() if key in _FIELDS}
    result["human_reason"] = human_reason(str(event.get("reason_code", "")))
    for key in _FINANCIAL:
        if isinstance(result.get(key), Decimal):
            result[key] = str(result[key])
    return result
