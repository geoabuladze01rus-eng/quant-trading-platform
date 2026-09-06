from collections import deque
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
    who: str = "system"
    when: str = ""
    why: str = ""
    decision: str = ""
    execution_id: str = ""
    reason_code: str = ""
    opportunity_id: str = ""

    def __post_init__(self) -> None:
        if not self.when:
            object.__setattr__(self, "when", self.timestamp)
        if not self.why:
            object.__setattr__(self, "why", self.reason)


class AuditLog:
    def __init__(self) -> None:
        self._events: deque[AuditEvent] = deque(maxlen=10_000)

    def record(
        self, event: str, reason: str, market_scope: MarketScope, strategy: str,
        *, who: str = "system", why: str | None = None, decision: str = "",
        execution_id: str = "", reason_code: str = "",
        opportunity_id: str = "",
    ) -> AuditEvent:
        item = AuditEvent(
            event, reason, market_scope, strategy, datetime.now(UTC).isoformat(), str(uuid4()),
            who=who, why=reason if why is None else why, decision=decision,
            execution_id=execution_id, reason_code=reason_code,
            opportunity_id=opportunity_id,
        )
        self._events.append(item)
        return item

    def list(self) -> list[dict[str, str]]:
        """Return detached snapshots; callers cannot mutate stored frozen events."""
        return [asdict(event) for event in reversed(self._events)]
