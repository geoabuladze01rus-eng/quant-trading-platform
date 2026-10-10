import sqlite3
from dataclasses import replace
from decimal import Decimal as D

import httpx
import pytest

from quant_trading_platform.execution_engine import Signal
from quant_trading_platform.okx_controller.demo_signal import (
    DEMO_ENDPOINT,
    DemoSignalConfig,
    DemoSignalSender,
    preview,
)
from quant_trading_platform.okx_controller.manager import ControlError

NOW = 1791620000000


def signal(**changes):
    return replace(
        Signal(
            "demo-1",
            "BTC/USDT",
            "LONG",
            "HIGH",
            D("78"),
            D("2"),
            "range",
            D(".01"),
            D(".01"),
            D(".5"),
            D("10"),
            D(".1"),
            NOW,
        ),
        **changes,
    )


def test_preview_redacted_exact_size_no_placeholders():
    config = DemoSignalConfig("test-secret")
    payload = preview(signal(), config, now_ms=NOW)
    assert payload["investmentType"] == "margin" and payload["amount"] == "10"
    assert payload["instrument"] == "BTC-USDT-SWAP"
    assert payload["signalToken"] == "[REDACTED]"
    assert "test-secret" not in repr(config) and "{{" not in str(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": "LOW"},
        {"direction": "SHORT"},
        {"symbol": "ETH/USDT"},
        {"rr": D("1")},
        {"timestamp_ms": NOW - 1001},
        {"timestamp_ms": NOW + 1},
        {"valid_until_ms": NOW - 1},
        {"notional_usd": D("11")},
        {"risk_usd": D(".11")},
        {"market_state": "risk_off"},
        {"volatility": D(".06")},
        {"score": D("84")},
    ],
)
def test_reject_unsafe_entry(changes):
    with pytest.raises(ControlError):
        preview(signal(**changes), DemoSignalConfig("test-secret"), now_ms=NOW)


def test_default_never_posts(monkeypatch):
    monkeypatch.setattr(httpx.Client, "post", lambda *a, **k: pytest.fail("network call"))
    connection = sqlite3.connect(":memory:")
    sender = DemoSignalSender(DemoSignalConfig("test-secret"), connection)
    assert sender.send_entry(signal(), now_ms=NOW) == "PREVIEW_ONLY"
    assert connection.execute("SELECT COUNT(*) FROM okx_demo_dispatch").fetchone()[0] == 0


def test_requires_verified_bot():
    sender = DemoSignalSender(
        DemoSignalConfig("test-secret", enabled=True), sqlite3.connect(":memory:")
    )
    with pytest.raises(ControlError, match="demo_bot_not_verified"):
        sender.send_entry(signal(), now_ms=NOW)


@pytest.mark.parametrize("outcome", ["success", "timeout", "redirect", "rejected"])
def test_one_post_no_retry_no_duplicate_and_slot_locked(monkeypatch, tmp_path, outcome):
    calls = []

    def post(client, url, **kwargs):
        assert client.follow_redirects is False
        assert url == DEMO_ENDPOINT
        assert kwargs["json"]["signalToken"] == "test-secret"
        calls.append(url)
        if outcome == "timeout":
            raise httpx.ReadTimeout("sensitive upstream text")
        return httpx.Response({"success": 200, "redirect": 302, "rejected": 400}[outcome])

    monkeypatch.setattr(httpx.Client, "post", post)
    db = tmp_path / "dispatch.sqlite"
    config = DemoSignalConfig("test-secret", enabled=True, demo_bot_verified=True)
    connection = sqlite3.connect(db)
    sender = DemoSignalSender(config, connection)
    result = sender.send_entry(signal(), now_ms=NOW)
    assert result != "FILLED"
    connection.close()
    connection = sqlite3.connect(db)
    sender = DemoSignalSender(config, connection)
    assert sender.send_entry(signal(), now_ms=NOW) == result
    assert len(calls) == 1
    with pytest.raises(ControlError, match="demo_position_or_pending_dispatch"):
        sender.send_entry(signal(signal_id="demo-2"), now_ms=NOW)
    with pytest.raises(ControlError, match="idempotency_conflict"):
        sender.send_entry(signal(timestamp_ms=NOW - 1), now_ms=NOW)
    assert "test-secret" not in str(
        connection.execute("SELECT * FROM okx_demo_dispatch").fetchall()
    )


def test_no_uncommitted_reservation():
    connection = sqlite3.connect(":memory:")
    sender = DemoSignalSender(
        DemoSignalConfig("test-secret", enabled=True, demo_bot_verified=True), connection
    )
    connection.execute("BEGIN")
    with pytest.raises(ControlError, match="demo_requires_durable_reservation"):
        sender.send_entry(signal(), now_ms=NOW)


@pytest.mark.parametrize("token", ["", "{{token}}", "white space"])
def test_bad_token(token):
    with pytest.raises(ControlError):
        DemoSignalConfig(token)


def test_margin_hard_limit():
    with pytest.raises(ControlError, match="demo_margin_limit"):
        DemoSignalConfig("test-secret", margin_usdt=D("11"))
