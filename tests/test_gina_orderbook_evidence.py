import asyncio
import json
from decimal import Decimal as D

import pytest

from quant_trading_platform.signal_watch.gina_orderbook import (
    GinaOrderBookProducer,
    adapt_gina_order_book,
)
from quant_trading_platform.signal_watch.providers import ProviderEvidenceCache

NOW = 1_000_000


def book():
    return [
        {
            "coin": "BTC",
            "side": "bid",
            "level": 1,
            "price": "100",
            "size": "9",
            "count": 2,
            "midPrice": "200",
            "timestamp": NOW,
        },
        {
            "coin": "BTC",
            "side": "ask",
            "level": 1,
            "price": "300",
            "size": "1",
            "count": 1,
            "midPrice": "200",
            "timestamp": NOW,
        },
    ]


def test_notional_flow_uses_exact_decimal_and_preserves_upstream_time():
    assert adapt_gina_order_book("BTC/USDT", json.dumps(book()), now_ms=NOW, depth=1) == {
        "symbol": "BTC/USDT",
        "observations": [
            {
                "domain": "D",
                "strength": "0.5",
                "timestamp_ms": NOW,
                "origin": "hyperliquid_canonical_usdc_depth",
            }
        ],
    }


@pytest.mark.parametrize(
    "fault",
    [
        "coin",
        "side",
        "duplicate",
        "missing",
        "clock",
        "future",
        "stale",
        "mixed_time",
        "mixed_mid",
        "mid",
        "crossed",
        "nan",
        "zero_size",
        "count",
        "level",
        "float_timestamp",
        "non_json",
    ],
)
def test_invalid_or_incomplete_gina_book_is_not_neutral_confirmation(fault):
    rows = book()
    if fault == "coin":
        rows[1]["coin"] = "ETH"
    elif fault == "side":
        rows[1]["side"] = "sell"
    elif fault == "duplicate":
        rows[1]["side"] = "bid"
    elif fault == "missing":
        rows.pop()
    elif fault == "clock":
        rows[0]["timestamp"] = 0
    elif fault == "future":
        rows[0]["timestamp"] = NOW + 1
    elif fault == "stale":
        for row in rows:
            row["timestamp"] = NOW - 5_001
    elif fault == "mixed_time":
        rows[1]["timestamp"] = NOW - 1
    elif fault == "mixed_mid":
        rows[1]["midPrice"] = "201"
    elif fault == "mid":
        for row in rows:
            row["midPrice"] = "202"
    elif fault == "crossed":
        rows[1]["price"] = "99"
    elif fault == "nan":
        rows[0]["price"] = "NaN"
    elif fault == "zero_size":
        rows[0]["size"] = "0"
    elif fault == "count":
        rows[0]["count"] = True
    elif fault == "level":
        rows[0]["level"] = 2
    elif fault == "float_timestamp":
        rows[0]["timestamp"] = float(NOW)
    raw = "chart summary only" if fault == "non_json" else json.dumps(rows)
    with pytest.raises(ValueError):
        adapt_gina_order_book("BTC/USDT", raw, now_ms=NOW, depth=1)


def test_measured_ask_dominance_is_zero_bullish_flow():
    rows = book()
    rows[0]["size"] = "1"
    assert (
        adapt_gina_order_book("BTC/USDT", json.dumps(rows), now_ms=NOW, depth=1)["observations"][0][
            "strength"
        ]
        == "0"
    )


@pytest.mark.asyncio
async def test_producer_invokes_only_public_book_tool_and_ignores_float_structured_content():
    async def execute(tool, arguments):
        assert tool == "gina_fetch_hyperliquid_orderbook"
        assert arguments == {"coin": "BTC", "depth": 1}
        rows = book()
        rows[0]["size"] = 9.0
        return {
            "content": [{"type": "text", "text": json.dumps(rows)}],
            "structuredContent": {"payload": {"made_up_ai_score": 1.0}},
        }

    cache = ProviderEvidenceCache()
    producer = GinaOrderBookProducer(cache, execute, depth=1, clock=lambda: NOW)
    assert await producer.collect("BTC/USDT")
    assert cache.observations("BTC/USDT", now_ms=NOW)[0].strength == D("0.5")


@pytest.mark.asyncio
async def test_failed_and_cancelled_producer_clear_only_the_affected_source():
    cache = ProviderEvidenceCache()
    report = adapt_gina_order_book("BTC/USDT", json.dumps(book()), now_ms=NOW, depth=1)
    cache.update("Gina", report, now_ms=NOW)
    cache.update("TraderSpy", report, now_ms=NOW)

    async def error(tool, arguments):
        return {"content": [{"type": "text", "text": "private error"}], "isError": True}

    producer = GinaOrderBookProducer(cache, error, depth=1, clock=lambda: NOW)
    assert not await producer.collect("BTC/USDT")
    assert [o.source for o in cache.observations("BTC/USDT", now_ms=NOW)] == ["TraderSpy"]
    assert producer.status["BTC/USDT"] == "provider_evidence_unavailable"
    cache.update("Gina", report, now_ms=NOW)
    entered = asyncio.Event()

    async def hang(tool, arguments):
        entered.set()
        await asyncio.Event().wait()

    producer = GinaOrderBookProducer(cache, hang, depth=1, clock=lambda: NOW)
    task = asyncio.create_task(producer.collect("BTC/USDT"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [o.source for o in cache.observations("BTC/USDT", now_ms=NOW)] == ["TraderSpy"]


@pytest.mark.asyncio
async def test_wrong_asset_never_calls_a_source():
    async def execute(tool, arguments):
        raise AssertionError("Unsupported asset must not invoke a tool")

    producer = GinaOrderBookProducer(ProviderEvidenceCache(), execute)
    with pytest.raises(ValueError):
        await producer.collect("LTC/USDT")
