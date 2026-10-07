from dataclasses import replace
from decimal import Decimal

import pytest

from quant_trading_platform.market_data.liquidations import (
    LiquidationWindow,
    parse_binance_liquidation,
    parse_bybit_liquidation,
    parse_okx_liquidation,
)

CASES = [
    (
        parse_binance_liquidation,
        {
            "e": "forceOrder",
            "E": 1000,
            "o": {"s": "BTCUSDT", "S": "SELL", "T": 1000, "q": "2", "p": "100", "ap": "99"},
        },
        "long",
        "base_asset",
    ),
    (
        parse_bybit_liquidation,
        {
            "topic": "allLiquidation.BTCUSDT",
            "ts": 1000,
            "data": [{"T": 1000, "s": "BTCUSDT", "S": "Buy", "v": "2", "p": "100"}],
        },
        "long",
        "base_asset",
    ),
    (
        parse_okx_liquidation,
        {
            "arg": {"channel": "liquidation-orders", "instType": "SWAP"},
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "instType": "SWAP",
                    "details": [
                        {"ts": "1000", "side": "sell", "posSide": "long", "sz": "2", "bkPx": "100"}
                    ],
                }
            ],
        },
        "long",
        "contracts",
    ),
]


@pytest.mark.parametrize("parser,payload,side,unit", CASES)
def test_parsing_provenance_and_window(parser, payload, side, unit):
    events = parser(payload, now_ms=1100)
    assert len(events) == 1
    event = events[0]
    assert event.symbol == "BTC/USDT"
    assert event.side == side
    assert event.quantity == Decimal("2")
    assert event.quantity_unit == unit
    if unit == "contracts":
        assert event.source_completeness == "partial_exchange_stream"
    store = LiquidationWindow(max_events=2)
    assert store.add(event, now_ms=1100)
    assert not store.add(event, now_ms=1100)
    store.add(replace(event, quantity=Decimal("3"), timestamp_ms=1100), now_ms=1100)
    summary = store.summary("BTC/USDT", 1100)
    row = summary["window_5m"]["venues"][0]
    assert row["event_count"] == 2
    assert row["long_quantity"] == "5"
    assert row["short_quantity"] == "0"
    assert row["largest_event_quantity"] == "3"
    assert row["latest_event_timestamp_ms"] == 1100
    assert store.summary("BTC/USDT", 301101)["window_5m"]["venues"] == []
    assert store.summary("BTC/USDT", 901101)["window_15m"]["venues"] == []
    assert store.summary("BTC/USDT", 3601101)["window_1h"]["venues"] == []


@pytest.mark.parametrize("parser,payload,side,unit", CASES)
def test_malformed_identity_nonfinite_and_future(parser, payload, side, unit):
    import json

    raw = json.dumps(payload)
    for invalid in [
        None,
        {},
        json.loads(raw.replace("BTC", "LTC")),
        json.loads(raw.replace('"2"', '"NaN"')),
    ]:
        with pytest.raises(ValueError):
            parser(invalid, now_ms=1100)
    with pytest.raises(ValueError):
        parser(payload, now_ms=999)


def test_opposite_side_and_all_market_payload():
    payload = CASES[0][1]
    import copy

    other = copy.deepcopy(payload)
    other["o"]["S"] = "BUY"
    events = parse_binance_liquidation([payload, other], now_ms=1100)
    assert [e.side for e in events] == ["long", "short"]
    events += parse_okx_liquidation(CASES[2][1], now_ms=1100)
    store = LiquidationWindow(max_events=2)
    for event in events:
        store.add(event, now_ms=1100)
    result = store.summary("BTC/USDT", 1100)["window_1h"]
    assert result["cross_venue_notional"] is None
    assert result["comparable"] is False
    assert sum(row["event_count"] for row in result["venues"]) == 2


@pytest.mark.asyncio
async def test_collectors_public_lifecycle_reconnect_and_isolation():
    import asyncio
    import json

    from quant_trading_platform.market_data.liquidations import PublicLiquidationCollector
    from quant_trading_platform.models import Venue

    sent, urls, attempts = [], [], []

    class Socket:
        async def send(self, message):
            sent.append(json.loads(message))

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not attempts:
                attempts.append(1)
                return json.dumps(CASES[2][1])
            await asyncio.Event().wait()

    class Connection:
        async def __aenter__(self):
            return Socket()

        async def __aexit__(self, *args):
            pass

    def connect(url, **kwargs):
        urls.append(url)
        return Connection()

    store = LiquidationWindow()
    collector = PublicLiquidationCollector(
        Venue.OKX, store, connect=connect, clock=lambda: 1100, reconnect_seconds=0.02
    )
    await collector.start()
    for _ in range(100):
        if store.events:
            break
        await asyncio.sleep(0.005)
    assert len(store.events) == 1
    assert urls == ["wss://ws.okx.com/ws/v5/public"]
    assert sent == [
        {"op": "subscribe", "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]}
    ]
    await asyncio.wait_for(collector.stop(), 0.5)
    assert collector.status == "stopped"
    count = []

    def failed(url, **kwargs):
        count.append(1)
        raise OSError("secret")

    bad = PublicLiquidationCollector(
        Venue.BINANCE, store, connect=failed, clock=lambda: 1100, reconnect_seconds=0.03
    )
    await bad.start()
    await asyncio.sleep(0.08)
    assert 1 <= len(count) <= 3
    assert bad.error == "public_liquidations_unavailable"
    assert len(store.events) == 1
    await bad.stop()


def test_summary_safe_during_concurrent_collection():
    import threading

    store = LiquidationWindow(max_events=10000)
    event = parse_binance_liquidation(CASES[0][1], now_ms=10000)[0]
    for n in range(5000):
        store.add(replace(event, timestamp_ms=1000 + n), now_ms=10000)
    stop = threading.Event()

    def append():
        n = 0
        while not stop.is_set():
            store.add(replace(event, timestamp_ms=6000 + n % 4000), now_ms=10000)
            n += 1

    thread = threading.Thread(target=append)
    thread.start()
    try:
        for _ in range(50):
            result = store.summary("BTC/USDT", 10000)
            assert result["window_5m"]["venues"][0]["event_count"] <= 10000
    finally:
        stop.set()
        thread.join(timeout=2)


@pytest.mark.parametrize("venue", ["binance", "okx"])
def test_collector_filters_unrelated_instruments_without_dropping_supported(venue):
    import copy

    from quant_trading_platform.market_data.liquidations import parse_supported_liquidations
    from quant_trading_platform.models import Venue

    if venue == "binance":
        other = copy.deepcopy(CASES[0][1])
        other["o"]["s"] = "DOGEUSDT"
        payload = [other, CASES[0][1]]
    else:
        payload = copy.deepcopy(CASES[2][1])
        other = copy.deepcopy(payload["data"][0])
        other["instId"] = "DOGE-USDT-SWAP"
        payload["data"].insert(0, other)
    events = parse_supported_liquidations(Venue(venue), payload, 1100)
    assert len(events) == 1
    assert events[0].symbol == "BTC/USDT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "venue,ack,pong,ping",
    [
        ("okx", {"event": "subscribe"}, "pong", "ping"),
        (
            "bybit",
            {"op": "subscribe", "success": True},
            '{"op":"ping","success":true}',
            '{"op": "ping"}',
        ),
    ],
)
async def test_application_heartbeat_keeps_idle_public_subscription_healthy(venue, ack, pong, ping):
    import asyncio
    import json

    from quant_trading_platform.market_data.liquidations import PublicLiquidationCollector
    from quant_trading_platform.models import Venue

    sent = []
    queue = asyncio.Queue()
    queue.put_nowait(json.dumps(ack))

    class Socket:
        async def send(self, message):
            sent.append(message)
            if message == ping:
                queue.put_nowait(pong)

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await queue.get()

    class Connection:
        async def __aenter__(self):
            return Socket()

        async def __aexit__(self, *args):
            pass

    collector = PublicLiquidationCollector(
        Venue(venue),
        LiquidationWindow(),
        connect=lambda *a, **kw: Connection(),
        heartbeat_seconds=0.01,
    )
    await collector.start()
    await asyncio.sleep(0.045)
    await collector.stop()
    assert ping in sent
    assert collector.error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [{"op": "subscribe", "success": False}, None])
async def test_rejected_or_silent_subscription_fails_closed(ack):
    import asyncio
    import json

    from quant_trading_platform.market_data.liquidations import PublicLiquidationCollector
    from quant_trading_platform.models import Venue

    class Socket:
        delivered = False

        async def send(self, message):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            if ack is not None and not self.delivered:
                self.delivered = True
                return json.dumps(ack)
            await asyncio.Event().wait()

    class Connection:
        async def __aenter__(self):
            return Socket()

        async def __aexit__(self, *args):
            pass

    collector = PublicLiquidationCollector(
        Venue.BYBIT,
        LiquidationWindow(),
        connect=lambda *a, **kw: Connection(),
        subscription_timeout_seconds=0.02,
        reconnect_seconds=0.1,
    )
    await collector.start()
    await asyncio.sleep(0.04)
    assert collector.status == "error"
    assert collector.error == "public_liquidations_unavailable"
    await collector.stop()
