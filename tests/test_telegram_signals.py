import unittest
from decimal import Decimal

from quant_trading_platform.notifications.telegram_signals import (
    SpotSignal,
    TelegramDeliveryError,
    TelegramSignalNotifier,
    format_signal,
    should_deliver,
)

FAKE_BOT_CREDENTIAL = "synthetic-token-123"


def make_signal(**overrides: object) -> SpotSignal:
    base: dict[str, object] = {
        "signal_id": "BTC-USDT-2026-10-10-1H",
        "pair": "BTC/USDT",
        "confidence": "HIGH",
        "current_price": Decimal("82600"),
        "entry_low": Decimal("82000"),
        "entry_high": Decimal("82400"),
        "stop_loss": Decimal("80500"),
        "take_profits": (Decimal("84000"), Decimal("86000"), Decimal("89000")),
        "risk_reward": Decimal("2.8"),
        "risk_pct": Decimal("0.5"),
        "confirmations": ("Восходящий тренд 4H", "Подтверждённый BOS"),
        "cancel_condition": "закрытие 1H ниже 80500",
        "expires_at": "2026-10-10 18:00 МСК",
        "checked_at": "2026-10-10 08:00 МСК",
        "source": "OKX public candles",
    }
    base.update(overrides)
    return SpotSignal(**base)  # type: ignore[arg-type]


class FakeTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((url, payload))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, dict)
        return response


class TelegramSignalTests(unittest.TestCase):
    def notifier(self, transport: FakeTransport) -> TelegramSignalNotifier:
        return TelegramSignalNotifier(
            chat_id="8999343417",
            _token=FAKE_BOT_CREDENTIAL,
            transport=transport,
            sleep=lambda _: None,
        )

    def test_medium_and_low_are_not_delivered(self) -> None:
        for level in ("MEDIUM", "LOW"):
            with self.subTest(level=level), self.assertRaises(ValueError):
                make_signal(confidence=level)

    def test_low_risk_reward_is_not_delivered(self) -> None:
        self.assertFalse(should_deliver(make_signal(risk_reward=Decimal("1.5"))))

    def test_high_and_very_high_are_delivered_once(self) -> None:
        transport = FakeTransport([{"ok": True}])
        notifier = self.notifier(transport)
        signal = make_signal()
        self.assertTrue(notifier.deliver(signal))
        self.assertFalse(notifier.deliver(signal))
        self.assertEqual(len(transport.calls), 1)
        url, payload = transport.calls[0]
        self.assertEqual(payload["chat_id"], "8999343417")
        self.assertIn("ID: BTC-USDT-2026-10-10-1H", str(payload["text"]))

    def test_very_high_is_delivered(self) -> None:
        transport = FakeTransport([{"ok": True}])
        self.assertTrue(self.notifier(transport).deliver(make_signal(confidence="VERY HIGH")))

    def test_token_is_not_exposed_in_repr(self) -> None:
        notifier = self.notifier(FakeTransport([]))
        self.assertNotIn(FAKE_BOT_CREDENTIAL, repr(notifier))

    def test_retries_network_errors_then_succeeds(self) -> None:
        transport = FakeTransport([OSError("boom"), OSError("boom"), {"ok": True}])
        self.assertTrue(self.notifier(transport).deliver(make_signal()))
        self.assertEqual(len(transport.calls), 3)

    def test_gives_up_after_max_attempts_without_marking_delivered(self) -> None:
        transport = FakeTransport([OSError("a"), OSError("b"), OSError("c")])
        notifier = self.notifier(transport)
        with self.assertRaises(TelegramDeliveryError) as ctx:
            notifier.deliver(make_signal())
        self.assertNotIn(FAKE_BOT_CREDENTIAL, str(ctx.exception))
        self.assertNotIn("BTC-USDT-2026-10-10-1H", notifier.delivered_ids)

    def test_rejected_message_raises(self) -> None:
        transport = FakeTransport([{"ok": False}])
        with self.assertRaises(TelegramDeliveryError):
            self.notifier(transport).deliver(make_signal())

    def test_format_contains_required_fields(self) -> None:
        text = format_signal(make_signal())
        for needle in (
            "Операция: BUY — SPOT",
            "Stop Loss: 80500",
            "Take Profit 3: 89000",
            "Условие отмены",
            "Срок актуальности",
            "Данные проверены",
        ):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
