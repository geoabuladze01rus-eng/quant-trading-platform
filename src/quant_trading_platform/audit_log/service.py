from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import uuid4

from quant_trading_platform.config import MarketScope


@dataclass(frozen=True)
class AuditEvent:
    event: str
    reason: str
    market_scope: MarketScope
    strategy: str
    timestamp: str
    id: str


class AuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(
        self, event: str, reason: str, market_scope: MarketScope, strategy: str
    ) -> AuditEvent:
        item = AuditEvent(
            event, reason, market_scope, strategy, datetime.now(UTC).isoformat(), str(uuid4())
        )
        self._events.append(item)
        return item

    def list(self) -> list[dict[str, str]]:
        return [asdict(event) for event in reversed(self._events)]
