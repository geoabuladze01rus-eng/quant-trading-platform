"""Exact-value DTOs for funded, local paper commands."""

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any


class PaperOrderStatus(StrEnum):
    CREATED = "created"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"


OPEN_STATUSES = frozenset(("created", "accepted", "partially_filled"))
ASSETS = ("USDT", "BTC", "ETH")


@dataclass(frozen=True)
class PaperCommand:
    account_id: str
    symbol: str
    buy_venue: str
    sell_venue: str
    notional_usd: Decimal

    def payload(self) -> dict[str, str]:
        return {
            "operation": "execute",
            "account_id": self.account_id,
            "symbol": self.symbol,
            "buy_venue": self.buy_venue,
            "sell_venue": self.sell_venue,
            "notional_usd": decimal_text(self.notional_usd),
        }


def decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("Число должно быть конечным")
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def canonical_hash(payload: dict[str, Any]) -> str:
    return sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ReconciliationIssue:
    reason_code: str
    human_reason: str
    affected_entity: str
    timestamp: str
    severity: str
    correlation_id: str | None = None


class PaperCommandError(ValueError):
    def __init__(self, reason_code: str, reason_text: str) -> None:
        self.reason_code = reason_code
        self.reason_text = reason_text
        super().__init__(reason_text)
