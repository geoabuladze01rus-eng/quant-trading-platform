import asyncio
from dataclasses import replace
from decimal import Decimal
from importlib import import_module
from threading import Event

import httpx
import pytest

from quant_trading_platform.market_data.models import (
    NormalizedOrderBook,
    OrderBookLevel,
    normalize_order_book,
)
from quant_trading_platform.market_data.service import MarketDataService, MultiMarketDataService
from quant_trading_platform.models import MarketQuote, Venue


class Source:
    def __init__(self, venue: Venue, bid: str = "99", ask: str = "100") -> None:
        self.venue, self.bid, self.ask = venue, bid, ask
        self.error = False
        self.closed = False
        self.calls = 0

    def get_order_book(self, symbol: str) -> NormalizedOrderBook:
        self.calls += 1
        if self.error:
            raise OSError("secret must not escape")
        return normalize_order_book(
            self.venue, symbol, [[self.bid, "0.1"]], [[self.ask, "0.1"]], 10_000, 10_000,
        )

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_multiple_okx_pairs_are_independent() -> None:
    cache: dict[tuple[str, str], MarketQuote] = {}
    btc_source, ltc_source = Source(Venue.OKX), Source(Venue.OKX)
    btc = MarketDataService([btc_source], cache, symbol="BTC/USDT", clock=lambda: 10_000)
    ltc = MarketDataService([ltc_source], cache, symbol="LTC/USDT", clock=lambda: 10_000)
    service = MultiMarketDataService([btc, ltc])
    await asyncio.gather(btc.poll_once(), ltc.poll_once())
    assert set(cache) == {("okx", "BTC/USDT"), ("okx", "LTC/USDT")}
    assert service.book_for_simulation(Venue.OKX, "LTC-USDT") is not None
    assert service.book_for_simulation(Venue.OKX, "ETH/USDT") is None
    ltc_source.error = True
    await ltc.poll_once()
    assert set(cache) == {("okx", "BTC/USDT")}
    assert [row["status"] for row in service.snapshot()] == ["ok", "error"]


@pytest.mark.asyncio
async def test_poll_isolates_failure_and_invalidates_old_quote() -> None:
    cache: dict[tuple[str, str], MarketQuote] = {}
    sources = [Source(Venue.BINANCE), Source(Venue.BYBIT)]
    service = MarketDataService(sources, cache, clock=lambda: 10_000)
    await service.poll_once()
    assert len(cache) == 2
    sources[0].error = True
    await service.poll_once()
    assert list(cache) == [("bybit", "BTC/USDT")]
    rows = service.snapshot()
    assert rows[0]["status"] == "error"
    assert rows[0]["error"] == "public_market_data_unavailable"
    assert rows[1]["status"] == "ok"
    assert "secret" not in str(rows)


@pytest.mark.asyncio
async def test_source_becomes_stale_without_get_side_effects() -> None:
    cache: dict[tuple[str, str], MarketQuote] = {}
    now = 10_000
    source = Source(Venue.OKX)
    service = MarketDataService([source], cache, clock=lambda: now)
    assert service.snapshot()[0]["status"] == "no_data"
    await service.poll_once()
    now = 12_000
    assert service.snapshot()[0]["status"] == "stale"
    assert service.snapshot()[0]["error"] == "stale_market_data"
    assert source.calls == 1
    await service.poll_once()
    assert not cache


@pytest.mark.asyncio
async def test_future_book_never_enters_cache() -> None:
    cache: dict[tuple[str, str], MarketQuote] = {}
    service = MarketDataService([Source(Venue.BINANCE)], cache, clock=lambda: 9_000)
    await service.poll_once()
    assert not cache
    assert service.snapshot()[0]["status"] == "error"


def test_duplicate_venues_are_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        MarketDataService([Source(Venue.OKX), Source(Venue.OKX)], {})


@pytest.mark.asyncio
async def test_injected_invalid_deep_level_never_enters_evidence_cache() -> None:
    class BadDepth(Source):
        def get_order_book(self, symbol: str) -> NormalizedOrderBook:
            book = super().get_order_book(symbol)
            return replace(book, bids=(*book.bids, OrderBookLevel(Decimal("98"), Decimal("-1"))))

    cache: dict[tuple[str, str], MarketQuote] = {}
    service = MarketDataService([BadDepth(Venue.OKX)], cache, clock=lambda: 10_000)
    await service.poll_once()
    assert cache == {}
    assert service.snapshot()[0]["status"] == "error"
    assert service.book_for_simulation(Venue.OKX, "BTC/USDT") is None


@pytest.mark.asyncio
async def test_stale_poll_notifies_quote_invalidation() -> None:
    updates = []
    now = 10_000
    service = MarketDataService(
        [Source(Venue.OKX)], {}, clock=lambda: now,
        on_update=lambda: updates.append(dict(service.cache)),
    )
    await service.poll_once()
    now = 12_000
    await service.poll_once()
    assert len(updates) == 2
    assert updates[-1] == {}


@pytest.mark.asyncio
async def test_callback_failure_does_not_kill_polling_or_expose_exception() -> None:
    source = Source(Venue.OKX)

    def broken_callback() -> None:
        raise RuntimeError("secret must not escape")

    service = MarketDataService(
        [source], {}, clock=lambda: 10_000, interval_seconds=0.25,
        on_update=broken_callback,
    )
    await service.start()
    try:
        async with asyncio.timeout(1.5):
            while source.calls < 2:
                await asyncio.sleep(0.01)
        row = service.snapshot()[0]
        assert row["status"] == "ok"
        assert row["observer_error"] == "market_observer_unavailable"
        assert "secret" not in str(row)
    finally:
        await service.stop()

    service.on_update = lambda: None
    await service.poll_once()
    assert service.snapshot()[0]["observer_error"] is None


@pytest.mark.asyncio
async def test_poll_lifecycle_closes_sources_without_leaking_task() -> None:
    source = Source(Venue.BINANCE)
    service = MarketDataService([source], {}, clock=lambda: 10_000, interval_seconds=0.25)
    before = set(asyncio.all_tasks())
    await service.start()
    await service.start()
    await asyncio.sleep(0.05)
    await service.stop()
    assert source.closed
    assert source.calls >= 1
    assert not (set(asyncio.all_tasks()) - before)


@pytest.mark.asyncio
async def test_slow_venue_does_not_delay_other_venue_polling() -> None:
    release = Event()

    class SlowSource(Source):
        def get_order_book(self, symbol: str) -> NormalizedOrderBook:
            release.wait(timeout=2)
            return super().get_order_book(symbol)

    fast = Source(Venue.BYBIT)
    slow = SlowSource(Venue.BINANCE)
    service = MarketDataService([slow, fast], {}, interval_seconds=0.25, clock=lambda: 10000)
    await service.start()
    try:
        async with asyncio.timeout(1.5):
            while fast.calls < 2:
                await asyncio.sleep(0.01)
        assert slow.calls == 0
        assert service.snapshot()[1]["status"] == "ok"
    finally:
        release.set()
        await service.stop()


@pytest.mark.asyncio
async def test_clock_rollback_cannot_advertise_future_book_as_ok() -> None:
    now = 10000
    service = MarketDataService([Source(Venue.BINANCE)], {}, clock=lambda: now)
    await service.poll_once()
    now = 9000
    assert service.snapshot()[0]["status"] == "error"


@pytest.mark.asyncio
async def test_disabled_lifespan_does_not_start_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from quant_trading_platform.config import Settings

    api = import_module("quant_trading_platform.api.app")
    monkeypatch.setattr(api, "settings", Settings(_env_file=None, public_market_data_enabled=False))
    async with api.app.router.lifespan_context(api.app):
        assert api.app.state.market_data is None
        assert all(item["status"] == "disabled" for item in api.venues()[:3])


@pytest.mark.asyncio
async def test_books_reach_api_risk_and_source_status(monkeypatch: pytest.MonkeyPatch) -> None:
    api = import_module("quant_trading_platform.api.app")
    cache: dict[tuple[str, str], MarketQuote] = {}
    service = MarketDataService(
        [Source(Venue.BINANCE), Source(Venue.BYBIT, "102", "103")],
        cache, clock=lambda: 10_000,
    )
    monkeypatch.setattr(api, "_QUOTE_CACHE", cache)
    monkeypatch.setattr(api, "now_ms", lambda: 10_000)
    monkeypatch.setattr("quant_trading_platform.risk.time", lambda: 10)
    monkeypatch.setattr(api.app.state, "market_data", service, raising=False)
    await service.poll_once()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test",
    ) as client:
        rows = (await client.get("/opportunities")).json()["opportunities"]
        buy = next(row for row in rows if row["buy_venue"] == "binance")
        assert buy["approved"] is True
        assert Decimal(buy["expected_net_pct"]) == Decimal("1.75")
        venues = (await client.get("/venues")).json()
        assert venues[0]["mode"] == "public_read_only"
        assert venues[0]["status"] == "ok"
        assert venues[-1]["mode"] == "sandbox"
        assert venues[-1]["status"] == "no_data"

