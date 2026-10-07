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
    ConfirmedDemoSignal,
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
    def routed(request):
        path = request.url.path
        if path == "/api/v5/account/config":
            return httpx.Response(200, json={"code": "0", "data": [{"uid": "demo-test-account"}]})
        if path == "/api/v5/account/instruments":
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        {
                            "instId": "BTC-USDT",
                            "instType": "SPOT",
                            "quoteCcy": "USDT",
                            "state": "live",
                            "tickSz": "0.1",
                            "lotSz": "0.01",
                            "minSz": "0.01",
                        }
                    ],
                },
            )
        if path == "/api/v5/account/trade-fee":
            return httpx.Response(
                200, json={"code": "0", "data": [{"maker": "-0.001", "taker": "-0.0015"}]}
            )
        if path == "/api/v5/account/balance":
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [{"totalEq": "1000", "details": [{"ccy": "USDT", "availBal": "1000"}]}],
                },
            )
        return handler(request)

    return OKXDemoBot(CREDS, tmp_path / "demo.db", httpx.MockTransport(routed), clock)


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
        assert payload["ordType"] == "fok"
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
    with pytest.raises(RuntimeError, match="Незавершённая"):
        instance.submit(intent("second"))
    instance.db.execute("UPDATE lifecycle SET state='canceled_empty'")
    instance.db.commit()
    instance.submit(intent("second"))
    instance.db.execute("UPDATE lifecycle SET state='canceled_empty'")
    instance.db.commit()
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
    assert not calls
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


def exchange(tmp_path, *, state="filled", filled="1", algo_state="live", now=NOW):
    requests = []
    data = {
        "state": state,
        "accFillSz": filled,
        "instId": "BTC-USDT",
        "instType": "SPOT",
        "clOrdId": intent().payload(NOW)["clOrdId"],
        "ordId": "42",
        "side": "buy",
        "tdMode": "cash",
        "sz": "1",
        "px": "100",
        "avgPx": "100",
        "fee": "0",
        "feeCcy": "USDT",
    }
    algo = {
        "instId": "BTC-USDT",
        "algoClOrdId": data["clOrdId"],
        "algoId": "algo-42",
        "side": "sell",
        "tdMode": "cash",
        "sz": "1",
        "state": algo_state,
        "slTriggerPx": "99.50",
        "slOrdPx": "-1",
        "tpTriggerPx": "103.0",
        "tpOrdPx": "-1",
        "ordIdList": ["43"],
    }
    child = {
        "ordId": "43",
        "instId": "BTC-USDT",
        "instType": "SPOT",
        "side": "sell",
        "tdMode": "cash",
        "sz": "1",
        "accFillSz": "1",
        "state": "filled",
        "avgPx": "103",
        "fee": "-0.103",
        "feeCcy": "USDT",
    }

    def handler(request):
        requests.append(request)
        assert request.headers["x-simulated-trading"] == "1"
        if request.method == "POST":
            return accepted(request)
        rows = (
            [algo]
            if request.url.path.endswith("order-algo")
            else ([child] if request.url.params.get("ordId") == "43" else [data])
        )
        return httpx.Response(200, json={"code": "0", "data": rows})

    instance = bot(tmp_path, handler)
    instance.submit(intent())
    instance.clock = lambda: now
    return instance, data, algo, child, requests, handler


def test_recovery_verifies_stop_and_full_exit_without_resubmitting_entry(tmp_path):
    instance, _, algo, _, calls, handler = exchange(tmp_path)
    assert instance.monitor("signal-1")["state"] == "protected"
    instance.close()
    restarted = bot(tmp_path, handler)
    assert restarted.recover()[0]["state"] == "protected"
    with pytest.raises(RuntimeError, match="Незавершённая"):
        restarted.submit(intent("second"))
    algo["state"] = "effective"
    result = restarted.monitor("signal-1")
    assert result["state"] == "closed" and result["exit_ordId"] == "43"
    assert result["realized_pnl_usdt"] == "2.897"
    restarted.submit(intent("second"))
    assert len([r for r in calls if r.method == "POST"]) == 2
    restarted.close()


@pytest.mark.parametrize(
    "change",
    [
        {"slTriggerPx": "98"},
        {"sz": "0.5"},
        {"side": "buy"},
        {"state": "canceled"},
        {"algoClOrdId": "other"},
        {"tpTriggerPx": "NaN"},
    ],
)
def test_wrong_or_inactive_protection_blocks_new_entry(tmp_path, change):
    instance, _, algo, _, _, _ = exchange(tmp_path)
    algo.update(change)
    assert instance.monitor("signal-1")["state"] in {
        "reconciliation_required",
        "exit_needs_reconciliation",
    }
    with pytest.raises(RuntimeError, match="Незавершённая"):
        instance.submit(intent("second"))
    instance.close()


def test_stale_entry_cancel_ack_does_not_mean_canceled_and_is_once_only(tmp_path):
    instance, data, _, _, calls, handler = exchange(
        tmp_path,
        state="live",
        filled="0",
        now=NOW + 31_000,
    )
    assert instance.monitor("signal-1")["state"] == "cancel_pending"
    instance.close()
    restarted = bot(tmp_path, handler, lambda: NOW + 31_000)
    assert restarted.recover()[0]["state"] == "cancel_pending"
    cancels = [r for r in calls if r.url.path.endswith("cancel-order")]
    assert len(cancels) == 1
    data["state"] = "canceled"
    assert restarted.monitor("signal-1")["state"] == "canceled_empty"
    restarted.close()


def test_partial_fill_is_flagged_and_remaining_entry_is_canceled(tmp_path):
    instance, data, _, _, calls, _ = exchange(tmp_path, state="partially_filled", filled="0.5")
    assert instance.monitor("signal-1")["state"] == "unprotected_partial"
    assert len([r for r in calls if r.url.path.endswith("cancel-order")]) == 1
    data["state"] = "canceled"
    assert instance.monitor("signal-1")["state"] == "unprotected_partial"
    with pytest.raises(RuntimeError, match="Незавершённая"):
        instance.submit(intent("second"))
    instance.close()


@pytest.mark.parametrize(
    "change",
    [
        {"state": "partially_filled", "accFillSz": "0.5"},
        {"instId": "ETH-USDT"},
        {"side": "buy"},
        {"sz": "2"},
    ],
)
def test_triggered_algo_is_not_mistaken_for_completed_exit(tmp_path, change):
    instance, _, _, child, _, _ = exchange(tmp_path, algo_state="effective")
    child.update(change)
    assert instance.monitor("signal-1")["state"] != "closed"
    instance.close()


def test_ambiguous_order_identity_halts_recovery(tmp_path):
    instance, data, _, _, _, _ = exchange(tmp_path)
    data["clOrdId"] = "foreign-order"
    assert instance.recover()[0]["state"] == "reconciliation_required"
    instance.close()


def confirmed():
    return ConfirmedDemoSignal(
        intent(),
        "HIGH",
        frozenset({"trend_4h", "structure_1h", "volume", "level_retest"}),
        NOW,
        NOW,
        True,
        False,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"confidence": "MEDIUM"},
        {"higher_timeframes_aligned": False},
        {"news_blocked": True},
        {"observed_at_ms": NOW - 15_001},
        {"news_checked_at_ms": NOW - 300_001},
        {"observed_at_ms": NOW + 1},
        {"confirmations": frozenset({"trend_4h", "structure_1h", "volume"})},
        {"confirmations": frozenset({"volume", "liquidity", "vwap", "level_retest"})},
    ],
)
def test_signal_bridge_rejects_missing_confirmations_and_stale_news(tmp_path, change):
    calls = []
    instance = bot(tmp_path, lambda request: calls.append(request))
    with pytest.raises(ValueError):
        instance.execute_signal(replace(confirmed(), **change))
    assert not calls
    assert not instance.db.execute("SELECT * FROM intents").fetchall()
    instance.close()


def test_signal_bridge_submits_a_valid_decision(tmp_path):
    instance = bot(tmp_path, accepted)
    assert instance.execute_signal(confirmed())["status"] == "accepted"
    instance.close()


@pytest.mark.parametrize("failure", ["rules", "fee", "balance", "invalid_balance"])
def test_preflight_failure_cannot_send_order_or_reserve_budget(tmp_path, failure):
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(request)
        if request.url.path.endswith("config"):
            rows = [{"uid": "demo-test-account"}]
        elif request.url.path.endswith("instruments"):
            rows = [
                {
                    "instId": "BTC-USDT",
                    "instType": "SPOT",
                    "quoteCcy": "USDT",
                    "state": "live",
                    "tickSz": "3" if failure == "rules" else "0.1",
                    "lotSz": "0.01",
                    "minSz": "0.01",
                }
            ]
        elif request.url.path.endswith("trade-fee"):
            rows = [{"maker": "-0.001", "taker": "-0.01" if failure == "fee" else "-0.001"}]
        else:
            rows = [
                {
                    "totalEq": "1000",
                    "details": [
                        {
                            "ccy": "USDT",
                            "availBal": "NaN" if failure == "invalid_balance" else "100",
                        }
                    ],
                }
            ]
        return httpx.Response(200, json={"code": "0", "data": rows})

    instance = OKXDemoBot(CREDS, tmp_path / "demo.db", httpx.MockTransport(handler), lambda: NOW)
    with pytest.raises((ValueError, RuntimeError)):
        instance.submit(intent())
    assert not posts and not instance.db.execute("SELECT * FROM intents").fetchall()
    instance.close()


def test_base_currency_fee_changes_protected_quantity_and_net_profit(tmp_path):
    instance, entry, algo, child, _, _ = exchange(tmp_path, algo_state="effective")
    entry.update(fee="-0.001", feeCcy="BTC")
    algo["sz"] = "0.999"
    child.update(sz="0.999", accFillSz="0.999")
    result = instance.monitor("signal-1")
    assert result["state"] == "closed"
    assert result["net_quantity"] == "0.999"
    assert result["realized_pnl_usdt"] == "2.794"
    instance.close()


def test_lost_ack_is_recovered_from_matching_exchange_order(tmp_path):
    instance, _, _, _, _, _ = exchange(tmp_path)
    instance.db.execute("UPDATE intents SET result=NULL")
    instance.db.execute("UPDATE lifecycle SET state='submission_unknown'")
    instance.db.commit()
    assert instance.recover()[0]["state"] == "protected"
    assert instance.submit(intent())["result"]["ordId"] == "42"
    instance.close()


def test_worker_records_only_changed_states_across_restart(tmp_path):
    from quant_trading_platform.paper_trading.okx_demo_runner import DemoRunner

    instance, _, algo, _, _, handler = exchange(tmp_path)
    events = []
    runner = DemoRunner(instance, None, emit=events.append)
    runner.tick()
    runner.tick()
    assert len(events) == 1 and events[0]["state"] == "protected"
    instance.close()
    restarted = bot(tmp_path, handler)
    runner = DemoRunner(restarted, None, emit=events.append)
    runner.tick()
    assert len(events) == 1
    algo["state"] = "effective"
    runner.tick()
    assert len(events) == 2 and events[-1]["state"] == "closed"
    assert restarted.db.execute("SELECT COUNT(*) FROM demo_events").fetchone()[0] == 2
    restarted.close()


def signal_json():
    return {
        "signal_id": "signal-1",
        "symbol": "BTC-USDT",
        "entry": "100",
        "quantity": "1",
        "stop": "99.5",
        "take_profit": "103",
        "expires_at_ms": NOW + 30_000,
        "confidence": "HIGH",
        "confirmations": ["trend_4h", "structure_1h", "volume", "level_retest"],
        "observed_at_ms": NOW,
        "news_checked_at_ms": NOW,
        "higher_timeframes_aligned": True,
        "news_blocked": False,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"entry": 100.0},
        {"news_blocked": "false"},
        {"observed_at_ms": True},
        {"confirmations": ["volume", "volume"]},
        {"extra": "field"},
    ],
)
def test_inbox_parser_rejects_ambiguous_types_and_duplicate_evidence(change):
    from quant_trading_platform.paper_trading.okx_demo_runner import parse_signal

    with pytest.raises(ValueError):
        parse_signal({**signal_json(), **change})


def test_worker_requires_enable_flag_and_waits_when_signal_is_missing(tmp_path):
    from quant_trading_platform.paper_trading.okx_demo_runner import DemoRunner

    calls = []

    def handler(request):
        calls.append(request)
        return accepted(request)

    instance = bot(tmp_path, handler)
    path = tmp_path / "signal.json"
    path.write_text(json.dumps(signal_json()))
    disabled = DemoRunner(instance, path)
    assert disabled.tick()["status"] == "monitoring"
    assert not calls
    path.unlink()
    enabled = DemoRunner(instance, path, entries_enabled=True)
    assert enabled.tick()["status"] == "waiting_for_signal"
    path.write_text(json.dumps({**signal_json(), "confidence": "MEDIUM"}))
    assert enabled.tick()["status"] == "signal_rejected"
    assert not calls
    path.write_text(json.dumps(signal_json()))
    assert enabled.tick()["status"] == "submitted"
    instance.close()


def test_signal_freshness_is_rechecked_after_preflight_before_post(tmp_path):
    ticks = iter([NOW, NOW, NOW, NOW + 16_000])
    calls = []
    instance = bot(tmp_path, lambda request: calls.append(request), lambda: next(ticks))
    with pytest.raises(ValueError, match="подтверждённого"):
        instance.execute_signal(confirmed())
    assert not calls
    instance.close()


def test_account_switch_cannot_cancel_or_submit_using_another_accounts_journal(tmp_path):
    instance, _, _, _, _, _ = exchange(tmp_path)
    instance.http.close()
    calls = []

    def other_account(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path.endswith("config")
        return httpx.Response(200, json={"code": "0", "data": [{"uid": "different-demo-account"}]})

    instance.http = httpx.Client(
        base_url="https://openapi.okx.com", transport=httpx.MockTransport(other_account)
    )
    with pytest.raises(RuntimeError, match="другому"):
        instance.monitor("signal-1")
    with pytest.raises(RuntimeError, match="другому"):
        instance.submit(intent("second"))
    assert len(calls) == 2
    instance.close()


def test_matching_client_id_with_wrong_exchange_id_does_not_close_position(tmp_path):
    instance, data, _, _, _, _ = exchange(tmp_path)
    data["ordId"] = "foreign-ordId"
    assert instance.monitor("signal-1")["state"] == "reconciliation_required"
    instance.close()


def test_realized_loss_stop_blocks_next_entry(tmp_path):
    instance, _, _, child, _, _ = exchange(tmp_path, algo_state="effective")
    child["avgPx"] = "97"
    assert instance.monitor("signal-1")["state"] == "closed"
    with pytest.raises(RuntimeError, match="убытка"):
        instance.submit(intent("second"))
    instance.close()


def test_base_currency_rebate_cannot_hide_remaining_inventory(tmp_path):
    instance, entry, _, _, _, _ = exchange(tmp_path, algo_state="effective")
    entry.update(fee="0.001", feeCcy="BTC")
    assert instance.monitor("signal-1")["state"] == "reconciliation_required"
    instance.close()
