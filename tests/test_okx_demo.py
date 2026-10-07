"""Exchange-demo boundary and crash/restart regression checks, without network."""

import base64
import hashlib
import hmac
import json
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from quant_trading_platform.paper_trading.okx_demo import (
    DemoCredentials,
    DemoIntent,
    OKXDemoBot,
)

NOW = 1_800_000_000_000
CREDS = DemoCredentials("test-key", "test-secret", "test-passphrase")


def intent(signal_id="signal-1"):
    return DemoIntent(
        signal_id,
        "BTC-USDT",
        Decimal("100"),
        Decimal("1"),
        Decimal("99.5"),
        Decimal("103"),
        NOW + 30_000,
    )


def bot(tmp_path, handler, clock=lambda: NOW):
    return OKXDemoBot(CREDS, tmp_path / "demo.db", httpx.MockTransport(handler), clock)


def accepted(request):
    return httpx.Response(200, json={"code": "0", "data": [{"sCode": "0", "ordId": "42"}]})


def test_signed_demo_only_cash_order_has_attached_stop_and_target(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "openapi.okx.com"
        assert request.headers["x-simulated-trading"] == "1"
        assert request.headers["expTime"] == str(NOW + 30_000)
        timestamp = request.headers["OK-ACCESS-TIMESTAMP"]
        expected = base64.b64encode(
            hmac.new(
                b"test-secret",
                timestamp.encode() + b"POST/api/v5/trade/order" + request.content,
                hashlib.sha256,
            ).digest()
        ).decode()
        assert request.headers["OK-ACCESS-SIGN"] == expected
        payload = json.loads(request.content)
        assert payload["side"] == "buy" and payload["tdMode"] == "cash"
        assert payload["ordType"] == "limit"
        assert payload["attachAlgoOrds"][0]["slTriggerPx"] == "99.5"
        assert payload["attachAlgoOrds"][0]["tpTriggerPx"] == "103"
        return accepted(request)

    instance = bot(tmp_path, handler)
    result = instance.submit(intent())
    assert result["status"] == "accepted" and result["filled"] is False
    instance.close()
    restarted = bot(tmp_path, handler)
    assert restarted.submit(intent())["status"] == "already_reserved"
    assert len(requests) == 1
    assert "test-secret" not in (tmp_path / "demo.db").read_bytes().decode(errors="ignore")
    restarted.close()


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "LTC-USDT"},
        {"symbol": "BTC-USDT-SWAP"},
        {"entry": Decimal("NaN")},
        {"quantity": Decimal("0")},
        {"stop": Decimal("101")},
        {"take_profit": Decimal("101")},
        {"quantity": Decimal("2")},
        {"stop": Decimal("98")},
        {"expires_at_ms": NOW},
        {"expires_at_ms": NOW + 60_001},
        {"signal_id": ""},
    ],
)
def test_invalid_or_risky_intent_never_contacts_exchange(tmp_path, change):
    calls = []
    instance = bot(tmp_path, lambda request: calls.append(request))
    with pytest.raises(ValueError):
        instance.submit(replace(intent(), **change))
    assert not calls
    instance.close()


def test_uncertain_submission_latches_across_restart_and_blocks_new_signals(tmp_path):
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("private response", request=request)

    instance = bot(tmp_path, timeout)
    with pytest.raises(RuntimeError) as error:
        instance.submit(intent())
    assert "private response" not in str(error.value)
    assert error.value.__suppress_context__
    instance.close()
    restarted = bot(tmp_path, timeout)
    assert restarted.submit(intent())["status"] == "already_reserved"
    with pytest.raises(RuntimeError, match="блокирует"):
        restarted.submit(intent("second"))
    assert len(calls) == 1
    restarted.close()


def test_changed_idempotent_payload_and_lifetime_budget_are_blocked(tmp_path):
    instance = bot(tmp_path, accepted)
    instance.submit(intent())
    with pytest.raises(ValueError, match="другим"):
        instance.submit(replace(intent(), quantity=Decimal("0.5")))
    instance.submit(intent("second"))
    with pytest.raises(ValueError, match="бюджет"):
        instance.submit(intent("third"))
    instance.close()


@pytest.mark.parametrize(
    "response",
    [
        {"code": "1", "msg": "secret"},
        {"code": "0", "data": []},
        {"code": "0", "data": [None]},
        {"code": "0", "data": [{"sCode": "1"}]},
    ],
)
def test_rejected_or_malformed_acknowledgement_never_counts_as_fill(tmp_path, response):
    instance = bot(tmp_path, lambda request: httpx.Response(200, json=response))
    with pytest.raises(RuntimeError):
        instance.submit(intent())
    assert instance.submit(intent())["result"] is None
    instance.close()


def test_read_only_balance_and_order_status(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["x-simulated-trading"] == "1"
        return accepted(request)

    instance = bot(tmp_path, handler)
    instance.check_connection()
    assert calls[0].method == "GET"
    instance.submit(intent())
    instance.order_status("signal-1")
    assert calls[-1].method == "GET" and "clOrdId=" in str(calls[-1].url)
    with pytest.raises(ValueError):
        instance.order_status("unknown")
    instance.close()


def test_expiry_after_reservation_prevents_network_submission(tmp_path):
    ticks = iter([NOW, NOW + 31_000])
    calls = []
    instance = bot(tmp_path, lambda request: calls.append(request), lambda: next(ticks))
    with pytest.raises(ValueError):
        instance.submit(intent())
    assert not calls
    instance.close()
