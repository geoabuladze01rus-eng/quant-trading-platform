import asyncio
from decimal import Decimal
from importlib import import_module
from threading import Event

import httpx
import pytest

from quant_trading_platform.crypto_universe import SpotInstrumentRules, crypto_spot_pair
from quant_trading_platform.market_data.models import NormalizedOrderBook, normalize_order_book
from quant_trading_platform.market_data.service import MarketDataService
from quant_trading_platform.models import MarketQuote, Venue


class Source:
    def __init__(self, venue: Venue, bid: str = "99", ask: str = "100") -> None:
        self.venue, self.bid, self.ask = venue, bid, ask
        self.error = False
        self.error_symbols: set[str] = set()
        self.closed = False
        self.close_calls = 0
        self.calls = 0
        self.called_symbols: list[str] = []

    def get_order_book(self, symbol: str) -> NormalizedOrderBook:
        self.calls += 1
        self.called_symbols.append(symbol)
        if self.error or symbol in self.error_symbols:
            raise OSError("secret must not escape")
        return normalize_order_book(
            self.venue, symbol, [[self.bid, "0.1"]], [[self.ask, "0.1"]], 10_000, 10_000,
        )

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


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
async def test_failed_update_callback_invalidates_pair_without_killing_polling() -> None:
    cache: dict[tuple[str, str], MarketQuote] = {}
    failed = True

    def callback() -> None:
        nonlocal failed
        if failed:
            failed = False
            raise RuntimeError("audit unavailable")

    source = Source(Venue.BINANCE)
    service = MarketDataService(
        [source], cache, clock=lambda: 10_000, on_update=callback
    )
    await service.poll_once()
    assert cache == {}
    assert service.snapshot()[0]["error"] == "market_update_processing_failed"

    await service.poll_once()
    assert list(cache) == [("binance", "BTC/USDT")]
    assert service.snapshot()[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_multi_symbol_polling_normalizes_deduplicates_and_snapshots_unique_pairs() -> None:
    cache: dict[tuple[str, str], MarketQuote] = {}
    sources = [Source(Venue.BINANCE), Source(Venue.OKX)]
    service = MarketDataService(
        sources,
        cache,
        symbol=["btcusdt", "ETH-USDT", "LTC_USDT", "BTC/USDT"],
        clock=lambda: 10_000,
    )

    assert service.symbols == ("BTC/USDT", "ETH/USDT", "LTC/USDT")
    assert service.symbol == "BTC/USDT"
    assert service.states[Venue.BINANCE] is service.states[(Venue.BINANCE, "BTC/USDT")]
    assert service.states[Venue.BINANCE] is service.pair_states[(Venue.BINANCE, "BTC/USDT")]

    await service.poll_once()

    expected_pairs = {
        (venue.value, symbol)
        for venue in (Venue.BINANCE, Venue.OKX)
        for symbol in service.symbols
    }
    assert set(cache) == expected_pairs
    assert set(sources[0].called_symbols) == set(service.symbols)
    assert set(sources[1].called_symbols) == set(service.symbols)
    rows = service.snapshot()
    assert len(rows) == len(expected_pairs)
    assert {(row["name"], row["symbol"]) for row in rows} == expected_pairs
    assert all(row["status"] == "ok" for row in rows)
    assert service.book_for_simulation(Venue.OKX, "ltcusdt").symbol == "LTC/USDT"


@pytest.mark.asyncio
async def test_multi_symbol_failure_isolated_to_one_venue_symbol_pair() -> None:
    cache: dict[tuple[str, str], MarketQuote] = {}
    sources = [Source(Venue.BINANCE), Source(Venue.BYBIT)]
    symbols = ["BTC/USDT", "ETH/USDT", "LTC/USDT"]
    service = MarketDataService(sources, cache, symbol=symbols, clock=lambda: 10_000)
    await service.poll_once()

    sources[0].error_symbols.add("ETH/USDT")
    await service.poll_once()

    failed = (Venue.BINANCE, "ETH/USDT")
    assert service.pair_states[failed].status == "error"
    assert service.pair_states[failed].error == "public_market_data_unavailable"
    assert ("binance", "ETH/USDT") not in cache
    assert service.book_for_simulation(*failed) is None
    assert service.states[Venue.BINANCE].status == "ok"
    assert service.book_for_simulation(Venue.BINANCE, "BTC/USDT") is not None
    assert service.book_for_simulation(Venue.BINANCE, "LTC/USDT") is not None
    assert service.book_for_simulation(Venue.BYBIT, "ETH/USDT") is not None
    assert len(cache) == 5


@pytest.mark.asyncio
async def test_required_instrument_rules_are_cached_and_exposed_per_pair() -> None:
    class RulesSource(Source):
        def __init__(self, venue: Venue) -> None:
            super().__init__(venue)
            self.rule_calls: list[str] = []

        def get_instrument_rules(self, symbol: str) -> SpotInstrumentRules:
            self.rule_calls.append(symbol)
            pair = crypto_spot_pair(symbol)
            return SpotInstrumentRules(
                self.venue,
                pair.symbol,
                pair.base_asset,
                pair.quote_asset,
                Decimal("0.01"),
                Decimal("0.00001"),
                Decimal("0.00001"),
                Decimal("5"),
                "trading",
                10_000,
                f"{self.venue.value}:public-instruments",
            )

    source = RulesSource(Venue.BYBIT)
    service = MarketDataService(
        [source],
        {},
        symbol=["BTC/USDT", "ETH/USDT", "LTC/USDT"],
        clock=lambda: 10_000,
        require_instrument_rules=True,
    )
    await service.poll_once()
    await service.poll_once()

    assert source.rule_calls == ["BTC/USDT", "ETH/USDT", "LTC/USDT"]
    rules = service.rules_for_simulation(Venue.BYBIT, "ltcusdt")
    assert rules is not None and rules.base_asset == "LTC"
    ltc = next(row for row in service.snapshot() if row["symbol"] == "LTC/USDT")
    assert ltc["instrument_status"] == "trading"
    assert ltc["base_asset"] == "LTC" and ltc["quote_asset"] == "USDT"
    assert ltc["min_notional"] == "5"


@pytest.mark.asyncio
async def test_required_instrument_rules_fail_closed_when_source_has_no_metadata() -> None:
    service = MarketDataService(
        [Source(Venue.BINANCE)],
        {},
        clock=lambda: 10_000,
        require_instrument_rules=True,
    )
    await service.poll_once()
    assert service.snapshot()[0]["status"] == "error"
    assert service.book_for_simulation(Venue.BINANCE, "BTC/USDT") is None
    assert service.rules_for_simulation(Venue.BINANCE, "BTC/USDT") is None


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


@pytest.mark.asyncio
async def test_poll_lifecycle_closes_sources_without_leaking_task() -> None:
    source = Source(Venue.BINANCE)
    service = MarketDataService(
        [source], {}, symbol=["BTC/USDT", "ETH/USDT", "LTC/USDT"],
        clock=lambda: 10_000, interval_seconds=0.25,
    )
    before = set(asyncio.all_tasks())
    await service.start()
    await service.start()
    await asyncio.sleep(0.05)
    await service.stop()
    await service.stop()
    assert source.closed
    assert source.close_calls == 1
    assert set(source.called_symbols) == set(service.symbols)
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
