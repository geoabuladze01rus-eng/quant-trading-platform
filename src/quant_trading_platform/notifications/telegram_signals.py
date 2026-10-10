"""Telegram delivery for confirmed spot signals (AGENTS.md safety rules apply).

Only HIGH and VERY HIGH signals are sent. Each signal has a unique id and is
delivered at most once. Messages are plain text, so no field can break parsing.
The bot token is never logged or included in exceptions or representations.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

logger = logging.getLogger(__name__)

ALLOWED_CONFIDENCE: Final[frozenset[str]] = frozenset({"HIGH", "VERY HIGH"})
TELEGRAM_API: Final[str] = "https://api.telegram.org"
MAX_ATTEMPTS: Final[int] = 3


@dataclass(frozen=True, slots=True)
class SpotSignal:
    signal_id: str
    pair: str
    confidence: str
    current_price: Decimal
    entry_low: Decimal
    entry_high: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, Decimal, Decimal]
    risk_reward: Decimal
    risk_pct: Decimal
    confirmations: tuple[str, ...]
    cancel_condition: str
    expires_at: str
    checked_at: str
    source: str

    def __post_init__(self) -> None:
        if self.confidence not in ALLOWED_CONFIDENCE:
            raise ValueError("only HIGH or VERY HIGH signals can be delivered")
        if not self.signal_id.strip():
            raise ValueError("signal_id is required")
        if not (self.stop_loss < self.entry_low <= self.entry_high):
            raise ValueError("stop loss must be below the entry range")
        if len(self.take_profits) != 3:
            raise ValueError("exactly three take-profit levels are required")


def should_deliver(signal: SpotSignal) -> bool:
    return signal.confidence in ALLOWED_CONFIDENCE and signal.risk_reward >= Decimal("2")


def format_signal(signal: SpotSignal) -> str:
    """Render the section 13 layout using only values from the signal itself."""
    lines = [
        "CRYPTO SPOT SIGNAL",
        f"ID: {signal.signal_id}",
        "Биржа: OKX",
        f"Актив: {signal.pair}",
        "Операция: BUY — SPOT",
        f"Уверенность: {signal.confidence}",
        f"Текущая цена: {signal.current_price}",
        f"Зона входа: {signal.entry_low}–{signal.entry_high}",
        f"Stop Loss: {signal.stop_loss}",
        f"Take Profit 1: {signal.take_profits[0]}",
        f"Take Profit 2: {signal.take_profits[1]}",
        f"Take Profit 3: {signal.take_profits[2]}",
        f"Risk/Reward: 1:{signal.risk_reward}",
        f"Риск капитала: {signal.risk_pct}%",
        "Подтверждения:",
        *[f"- {item}" for item in signal.confirmations],
        f"Условие отмены: {signal.cancel_condition}",
        f"Срок актуальности: {signal.expires_at}",
        f"Данные проверены: {signal.checked_at}, источник: {signal.source}",
        "Покупка только в указанном диапазоне. Это рекомендация, а не автоматический ордер.",
    ]
    return "\n".join(lines)


Transport = Callable[[str, dict[str, object]], dict[str, object]]


def _default_transport(url: str, payload: dict[str, object]) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise RuntimeError("unexpected Telegram response shape")
    return body


class TelegramDeliveryError(RuntimeError):
    pass


@dataclass
class TelegramSignalNotifier:
    chat_id: str
    _token: str = field(repr=False)
    transport: Transport = _default_transport
    sleep: Callable[[float], None] = time.sleep
    delivered_ids: set[str] = field(default_factory=set)

    def _send_text(self, text: str) -> None:
        url = f"{TELEGRAM_API}/bot{self._token}/sendMessage"
        payload: dict[str, object] = {"chat_id": self.chat_id, "text": text}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                body = self.transport(url, payload)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == MAX_ATTEMPTS:
                    raise TelegramDeliveryError("network error while sending") from None
                logger.warning("telegram send attempt %s failed: %s", attempt, type(exc).__name__)
                self.sleep(2 ** (attempt - 1))
                continue
            if body.get("ok") is True:
                return
            raise TelegramDeliveryError("telegram rejected the message")

    def deliver(self, signal: SpotSignal) -> bool:
        """Send a signal once. Returns False if skipped as ineligible or duplicate."""
        if not should_deliver(signal):
            return False
        if signal.signal_id in self.delivered_ids:
            return False
        self._send_text(format_signal(signal))
        self.delivered_ids.add(signal.signal_id)
        return True

    def deliver_all(self, signals: Iterable[SpotSignal]) -> int:
        return sum(1 for signal in signals if self.deliver(signal))
