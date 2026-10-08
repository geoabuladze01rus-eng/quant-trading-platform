import json
from decimal import Decimal as D

import pytest

from quant_trading_platform.signal_watch.providers import ProviderEvidenceCache
from quant_trading_platform.signal_watch.traderspy import TraderSpyProducer, adapt_traderspy

NOW = 1_200_010
OPEN = 1_140_000


def payloads():
    candles = {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "candles": [
            {
                "openTime": OPEN - 60_000,
                "closeTime": OPEN - 1,
                "isFinal": True,
                "open": "99",
                "high": "101",
                "low": "98",
                "close": "100",
                "volume": "10",
            },
            {
                "openTime": OPEN,
                "closeTime": OPEN + 59_999,
                "isFinal": True,
                "open": "100",
                "high": "103",
                "low": "99",
                "close": "102",
                "volume": "20",
            },
        ],
    }
    indicators = {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "lastCandleOpenTime": OPEN,
        "price": "102",
        "warnings": [],
        "indicators": {
            "ema": {
                "values": [
                    {"period": 20, "value": "101"},
                    {"period": 50, "value": "100"},
                    {"period": 200, "value": "99"},
                ]
            },
            "adx": {"value": "40", "plusDI": "35", "minusDI": "10"},
        },
    }
    return candles, indicators


def test_adapter_uses_exact_completed_bar_and_measured_strength():
    c, i = payloads()
    report = adapt_traderspy("BTC/USDT", json.dumps(c), json.dumps(i), now_ms=NOW)
    cache = ProviderEvidenceCache()
    assert cache.update("TraderSpy", report, now_ms=NOW)
    observation = cache.observations("BTC/USDT", now_ms=NOW)[0]
    assert observation.strength == D("0.8")
    assert observation.timestamp_ms == OPEN + 60_000
    assert observation.origin == "binance_usdm_candles"
    assert observation.domain == "A"


@pytest.mark.parametrize(
    "fault",
    [
        "identity",
        "indicator_time",
        "not_final",
        "stale",
        "future",
        "nan",
        "negative",
        "gap",
        "duplicate_period",
        "warnings",
    ],
)
def test_ambiguous_or_invalid_source_fails_closed(fault):
    c, i = payloads()
    now = NOW
    if fault == "identity":
        i["symbol"] = "ETHUSDT"
    elif fault == "indicator_time":
        i["lastCandleOpenTime"] = OPEN - 60_000
    elif fault == "not_final":
        c["candles"][-1]["isFinal"] = False
    elif fault == "stale":
        now += 60_001
    elif fault == "future":
        now = OPEN + 59_999
    elif fault == "nan":
        i["indicators"]["adx"]["value"] = "NaN"
    elif fault == "negative":
        c["candles"][-1]["volume"] = "-1"
    elif fault == "gap":
        c["candles"][0]["openTime"] -= 60_000
    elif fault == "duplicate_period":
        i["indicators"]["ema"]["values"][1]["period"] = 20
    else:
        i["warnings"] = ["insufficient_data"]
    with pytest.raises(ValueError):
        adapt_traderspy("BTC/USDT", json.dumps(c), json.dumps(i), now_ms=now)


def test_non_bullish_data_is_zero_evidence_not_opposite_trade_directive():
    c, i = payloads()
    i["indicators"]["adx"]["plusDI"] = "1"
    report = adapt_traderspy("BTC/USDT", json.dumps(c), json.dumps(i), now_ms=NOW)
    assert report["observations"][0]["strength"] == "0"
    assert "BUY" not in json.dumps(report) and "SELL" not in json.dumps(report)


@pytest.mark.asyncio
async def test_producer_only_invokes_allowlisted_read_tools_and_caches_result():
    c, i = payloads()
    calls = []

    async def execute(tool, arguments):
        calls.append((tool, arguments))
        payload = c if tool == "traderspy_get_candles" else i
        return {
            "content": [
                {"type": "text", "text": "untrusted summary"},
                {"type": "text", "text": json.dumps(payload)},
            ]
        }

    cache = ProviderEvidenceCache()
    producer = TraderSpyProducer(cache, execute, clock=lambda: NOW)
    assert await producer.collect("BTC/USDT")
    assert {call[0] for call in calls} == {
        "traderspy_get_candles",
        "traderspy_get_technical_indicators",
    }
    assert cache.observations("BTC/USDT", now_ms=NOW)


@pytest.mark.asyncio
async def test_failed_producer_invalidates_old_evidence_without_exposing_exception():
    cache = ProviderEvidenceCache()
    c, i = payloads()
    cache.update(
        "TraderSpy",
        adapt_traderspy("BTC/USDT", json.dumps(c), json.dumps(i), now_ms=NOW),
        now_ms=NOW,
    )

    async def execute(tool, arguments):
        raise RuntimeError("secret upstream details")

    producer = TraderSpyProducer(cache, execute, clock=lambda: NOW)
    assert not await producer.collect("BTC/USDT")
    assert not cache.observations("BTC/USDT", now_ms=NOW)
    assert producer.status["BTC/USDT"] == "provider_evidence_unavailable"


@pytest.mark.asyncio
async def test_cancelled_collection_invalidates_source_and_propagates_cancellation():
    import asyncio

    cache = ProviderEvidenceCache()
    c, i = payloads()
    cache.update(
        "TraderSpy",
        adapt_traderspy("BTC/USDT", json.dumps(c), json.dumps(i), now_ms=NOW),
        now_ms=NOW,
    )
    entered = asyncio.Event()

    async def execute(tool, arguments):
        entered.set()
        await asyncio.Event().wait()

    producer = TraderSpyProducer(cache, execute, clock=lambda: NOW)
    task = asyncio.create_task(producer.collect("BTC/USDT"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not cache.observations("BTC/USDT", now_ms=NOW)
    assert producer.status["BTC/USDT"] == "provider_evidence_unavailable"
