import asyncio
import json
from decimal import Decimal

import pytest

from quant_trading_platform.market_data.liquidations import (
    BINANCE_LIQUIDATION_URL,
    BYBIT_LIQUIDATION_URL,
    OKX_LIQUIDATION_URL,
    LiquidationCollector,
    LiquidationWindow,
    parse_binance_liquidation,
    parse_bybit_liquidation,
    parse_okx_liquidation,
)
from quant_trading_platform.models import Venue


def test_parses_binance_usdm_liquidation_and_side_semantics() -> None:
    events = parse_binance_liquidation({
        "e": "forceOrder",
        "E": 9_950,
        "o": {
            "s": "BTCUSDT",
            "S": "SELL",
            "q": "0.014",
            "p": "9910",
            "ap": "9911",
            "z": "0.014",
            "T": 9_940,
        },
        "ps": "BTCUSDT",
        "st": 1,
    }, now_ms=10_000)
    assert len(events) == 1
    event = events[0]
    assert event.venue == Venue.BINANCE
    assert event.symbol == "BTC/USDT"
    assert event.side == "long"
    assert event.quantity == Decimal("0.014")
    assert event.price == Decimal("9911")
    assert event.quantity_unit == "BTC"
    assert event.source_completeness == "latest_per_1000ms_snapshot"


def test_binance_ignores_cm_and_unapproved_symbols() -> None:
    base = {
        "e": "forceOrder",
        "E": 9_950,
        "o": {
            "s": "BTCUSDT", "S": "BUY", "q": "1", "p": "100",
            "ap": "100", "z": "1", "T": 9_940,
        },
        "ps": "BTCUSDT",
    }
    assert parse_binance_liquidation({**base, "st": 2}, now_ms=10_000) == ()
    other = {**base, "st": 1, "o": {**base["o"], "s": "DOGEUSDT"}}
    assert parse_binance_liquidation(other, now_ms=10_000) == ()


def test_parses_bybit_all_liquidation_array() -> None:
    events = parse_bybit_liquidation({
        "topic": "allLiquidation.ETHUSDT",
        "type": "snapshot",
        "ts": 9_980,
        "data": [
            {"T": 9_950, "s": "ETHUSDT", "S": "Buy", "v": "2.5", "p": "2000"},
            {"T": 9_960, "s": "ETHUSDT", "S": "Sell", "v": "1.0", "p": "1999"},
        ],
    }, now_ms=10_000)
    assert [event.side for event in events] == ["long", "short"]
    assert [event.quantity for event in events] == [Decimal("2.5"), Decimal("1.0")]
    assert all(event.quantity_unit == "ETH" for event in events)
    assert all(event.source_completeness == "all_liquidations_stream" for event in events)


def test_parses_okx_swap_details_and_marks_stream_partial() -> None:
    events = parse_okx_liquidation({
        "arg": {"channel": "liquidation-orders", "instType": "SWAP"},
        "data": [{
            "details": [{
                "bkLoss": "0",
                "bkPx": "150",
                "ccy": "",
                "posSide": "short",
                "side": "buy",
                "sz": "13",
                "ts": "9950",
            }],
            "instFamily": "SOL-USDT",
            "instId": "SOL-USDT-SWAP",
            "instType": "SWAP",
            "uly": "SOL-USDT",
        }],
    }, now_ms=10_000)
    assert len(events) == 1
    event = events[0]
    assert event.venue == Venue.OKX
    assert event.symbol == "SOL/USDT"
    assert event.side == "short"
    assert event.quantity == Decimal("13")
    assert event.quantity_unit == "contracts"
    assert event.price == Decimal("150")
    assert event.source_completeness == "partial_exchange_stream"


@pytest.mark.parametrize(
    "parser,payload",
    [
        (
            parse_binance_liquidation,
            {
                "e": "forceOrder", "E": 9_950, "st": 1,
                "o": {
                    "s": "BTCUSDT", "S": "SELL", "q": "1", "p": "100",
                    "ap": "100", "z": "NaN", "T": 9_940,
                },
            },
        ),
        (
            parse_bybit_liquidation,
            {
                "topic": "allLiquidation.BTCUSDT", "ts": 9_950,
                "data": [{"T": 9_940, "s": "BTCUSDT", "S": "Buy", "v": "-1", "p": "100"}],
            },
        ),
        (
            parse_okx_liquidation,
            {
                "arg": {"channel": "liquidation-orders", "instType": "SWAP"},
                "data": [{
                    "instId": "BTC-USDT-SWAP", "instType": "SWAP",
                    "details": [{
                        "bkPx": "100", "posSide": "long", "side": "sell",
                        "sz": "Infinity", "ts": "9940",
                    }],
                }],
            },
        ),
    ],
)
def test_rejects_nonfinite_or_nonpositive_liquidation_quantities(
    parser: object, payload: object,
) -> None:
    with pytest.raises(ValueError, match="quantity"):
        parser(payload, now_ms=10_000)  # type: ignore[operator]


@pytest.mark.parametrize(
    "parser,payload",
    [
        (
            parse_binance_liquidation,
            {
                "e": "forceOrder", "E": 10_100, "st": 1,
                "o": {
                    "s": "BTCUSDT", "S": "SELL", "q": "1", "p": "100",
                    "ap": "100", "z": "1", "T": 10_100,
                },
            },
        ),
        (
            parse_bybit_liquidation,
            {
                "topic": "allLiquidation.BTCUSDT", "ts": 10_100,
                "data": [{"T": 10_100, "s": "BTCUSDT", "S": "Buy", "v": "1", "p": "100"}],
            },
        ),
    ],
)
def test_rejects_future_liquidation_timestamps(parser: object, payload: object) -> None:
    with pytest.raises(ValueError, match="Future"):
        parser(payload, now_ms=10_000)  # type: ignore[operator]


def test_rolling_windows_prune_and_deduplicate_identical_events() -> None:
    window = LiquidationWindow(max_events=10)
    first = parse_binance_liquidation({
        "e": "forceOrder", "E": 9_950, "st": 1,
        "o": {
            "s": "BTCUSDT", "S": "SELL", "q": "1", "p": "100",
            "ap": "100", "z": "1", "T": 9_950,
        },
    }, now_ms=10_000)[0]
    second = parse_binance_liquidation({
        "e": "forceOrder", "E": 9_970, "st": 1,
        "o": {
            "s": "BTCUSDT", "S": "BUY", "q": "2", "p": "101",
            "ap": "101", "z": "2", "T": 9_970,
        },
    }, now_ms=10_000)[0]
    assert window.add(first) is True
    assert window.add(first) is False
    assert window.add(second) is True

    summary = window.summary("BTC/USDT", now_ms=10_000)
    assert summary["window_5m"]["event_count"] == 2
    assert summary["window_5m"]["long_quantity_by_unit"] == {"BTC": "1"}
    assert summary["window_5m"]["short_quantity_by_unit"] == {"BTC": "2"}
    assert summary["window_5m"]["largest_event"]["quantity"] == "2"
    assert summary["window_5m"]["latest_event_timestamp_ms"] == 9_970


def test_rolling_window_never_sums_incompatible_units() -> None:
    window = LiquidationWindow(max_events=10)
    binance = parse_binance_liquidation({
        "e": "forceOrder", "E": 9_950, "st": 1,
        "o": {
            "s": "BTCUSDT", "S": "SELL", "q": "1", "p": "100",
            "ap": "100", "z": "1", "T": 9_950,
        },
    }, now_ms=10_000)[0]
    okx = parse_okx_liquidation({
        "arg": {"channel": "liquidation-orders", "instType": "SWAP"},
        "data": [{
            "instId": "BTC-USDT-SWAP", "instType": "SWAP",
            "details": [{
                "bkPx": "100", "posSide": "long", "side": "sell",
                "sz": "5", "ts": "9960",
            }],
        }],
    }, now_ms=10_000)[0]
    window.add(binance)
    window.add(okx)
    summary = window.summary("BTC/USDT", now_ms=10_000)["window_5m"]
    assert summary["long_quantity_by_unit"] == {"BTC": "1", "contracts": "5"}
    assert "long_quantity" not in summary


def test_summary_expires_events_outside_one_hour() -> None:
    window = LiquidationWindow(max_events=10)
    old = parse_bybit_liquidation({
        "topic": "allLiquidation.BTCUSDT", "ts": 1_000,
        "data": [{"T": 1_000, "s": "BTCUSDT", "S": "Buy", "v": "1", "p": "100"}],
    }, now_ms=1_000)[0]
    window.add(old)
    assert window.summary("BTC/USDT", now_ms=3_601_001)["window_1h"]["event_count"] == 0


class FakeSocket:
    def __init__(self, messages: list[str]) -> None:
        self.messages = list(messages)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self) -> "FakeSocket":
        return self

    async def __anext__(self) -> str:
        if self.messages:
            return self.messages.pop(0)
        raise StopAsyncIteration


class FakeContext:
    def __init__(self, socket: FakeSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> FakeSocket:
        return self.socket

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("venue", "expected_url"),
    [
        (Venue.BINANCE, BINANCE_LIQUIDATION_URL),
        (Venue.BYBIT, BYBIT_LIQUIDATION_URL),
        (Venue.OKX, OKX_LIQUIDATION_URL),
    ],
)
async def test_collectors_use_only_public_urls_and_no_auth_messages(
    venue: Venue, expected_url: str,
) -> None:
    sockets: list[FakeSocket] = []
    urls: list[str] = []

    def connect(url: str) -> FakeContext:
        urls.append(url)
        socket = FakeSocket([])
        sockets.append(socket)
        return FakeContext(socket)

    collector = LiquidationCollector(
        venue,
        LiquidationWindow(max_events=10),
        connect_factory=connect,
        reconnect_delay_seconds=0.25,
    )
    await collector.start()
    await asyncio.sleep(0.03)
    await collector.stop()

    assert urls and all(url == expected_url for url in urls)
    assert ":8443" not in expected_url
    sent = [json.loads(message) for socket in sockets for message in socket.sent]
    assert all("login" not in message and "apiKey" not in str(message) for message in sent)
    if venue == Venue.BYBIT:
        assert sent[0]["args"] == [
            "allLiquidation.BTCUSDT",
            "allLiquidation.ETHUSDT",
            "allLiquidation.SOLUSDT",
        ]
    if venue == Venue.OKX:
        assert sent[0]["args"] == [{"channel": "liquidation-orders", "instType": "SWAP"}]


@pytest.mark.asyncio
async def test_collector_parses_messages_and_stops_cleanly() -> None:
    payload = json.dumps({
        "topic": "allLiquidation.BTCUSDT",
        "type": "snapshot",
        "ts": 9_980,
        "data": [{"T": 9_950, "s": "BTCUSDT", "S": "Buy", "v": "2", "p": "100"}],
    })
    calls = 0
    window = LiquidationWindow(max_events=10)

    def connect(_: str) -> FakeContext:
        nonlocal calls
        calls += 1
        return FakeContext(FakeSocket([payload]))

    collector = LiquidationCollector(
        Venue.BYBIT,
        window,
        connect_factory=connect,
        reconnect_delay_seconds=0.25,
        clock=lambda: 10_000,
    )
    await collector.start()
    async with asyncio.timeout(1):
        while window.summary("BTC/USDT", now_ms=10_000)["window_5m"]["event_count"] < 1:
            await asyncio.sleep(0.01)
    await collector.stop()
    assert calls >= 1
    assert window.summary("BTC/USDT", now_ms=10_000)["window_5m"]["event_count"] == 1
