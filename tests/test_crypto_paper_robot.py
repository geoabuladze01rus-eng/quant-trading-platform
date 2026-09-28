from decimal import Decimal
from time import time

import pytest

from quant_trading_platform.audit_log import PersistentAuditLog
from quant_trading_platform.config import Settings
from quant_trading_platform.crypto_universe import SpotInstrumentRules
from quant_trading_platform.market_data.models import NormalizedOrderBook, normalize_order_book
from quant_trading_platform.models import Venue
from quant_trading_platform.paper_trading.robot import CryptoPaperRobot
from quant_trading_platform.paper_trading.service import PersistentPaperService
from quant_trading_platform.persistence.store import SQLitePaperStore


class Markets:
    def __init__(self, books: dict[tuple[Venue, str], NormalizedOrderBook], now_ms: int) -> None:
        self.books = books
        self.now_ms = now_ms

    def book_for_simulation(
        self, venue: Venue, symbol: str
    ) -> NormalizedOrderBook | None:
        return self.books.get((venue, symbol))

    def rules_for_simulation(self, venue: Venue, symbol: str) -> SpotInstrumentRules | None:
        if (venue, symbol) not in self.books:
            return None
        base, quote = symbol.split("/")
        return SpotInstrumentRules(
            venue=venue,
            symbol=symbol,
            base_asset=base,
            quote_asset=quote,
            tick_size=Decimal("0.01"),
            quantity_step=Decimal("0.0001"),
            min_quantity=Decimal("0.0001"),
            min_notional=Decimal("5"),
            status="trading",
            timestamp_ms=self.now_ms,
            source=f"{venue.value}:test",
        )


def market(symbol: str, now_ms: int, *, stale: bool = False) -> Markets:
    timestamp = now_ms - 10_000 if stale else now_ms
    return Markets(
        {
            (Venue.BYBIT, symbol): normalize_order_book(
                Venue.BYBIT,
                symbol,
                [["99.00", "20"]],
                [["100.00", "20"]],
                timestamp,
                timestamp,
                max_age_ms=20_000,
            ),
            (Venue.OKX, symbol): normalize_order_book(
                Venue.OKX,
                symbol,
                [["101.00", "20"]],
                [["102.00", "20"]],
                timestamp,
                timestamp,
                max_age_ms=20_000,
            ),
        },
        now_ms,
    )


def robot(tmp_path, **overrides: object) -> tuple[CryptoPaperRobot, PersistentPaperService]:
    store = SQLitePaperStore(tmp_path / "robot.db")
    store.seed_account(
        "crypto-trial",
        balances={
            "USDT": Decimal("100000"),
            "BTC": Decimal("1"),
            "ETH": Decimal("10"),
            "LTC": Decimal("10"),
        }
    )
    service = PersistentPaperService(store)
    assert service.recover("crypto-trial")["status"] == "ok"
    settings = Settings(
        _env_file=None,
        paper_account_id="crypto-trial",
        crypto_paper_robot_enabled=True,
        crypto_paper_robot_interval_seconds=30,
        **overrides,
    )
    return CryptoPaperRobot(settings, service, PersistentAuditLog(store)), service


@pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USDT", "LTC/USDT"])
def test_each_supported_asset_can_execute_only_a_positive_paper_signal(tmp_path, symbol):
    instance, service = robot(tmp_path)
    now_ms = int(time() * 1000)

    result = instance.run_once(market(symbol, now_ms), now_ms=now_ms)

    assert result["state"] == "filled"
    assert result["last_signal"]["symbol"] == symbol
    assert Decimal(result["last_signal"]["net_edge_pct"]) > 0
    orders = service.store.list_orders("crypto-trial")
    fills = service.store.list_fills("crypto-trial")
    assert len(orders) == 1 and orders[0]["symbol"] == symbol
    assert len(fills) == 2
    assert service.reconcile("crypto-trial")["status"] == "ok"


def test_stale_asset_is_blocked_without_stopping_fresh_assets(tmp_path):
    instance, service = robot(tmp_path)
    now_ms = int(time() * 1000)
    books = market("BTC/USDT", now_ms, stale=True).books
    books.update(market("ETH/USDT", now_ms).books)

    result = instance.run_once(Markets(books, now_ms), now_ms=now_ms)

    states = {row["symbol"]: row for row in result["symbol_states"]}
    assert states["BTC/USDT"]["reason_code"] == "stale_market_data"
    assert states["ETH/USDT"]["state"] == "filled"
    assert service.store.list_orders()[0]["symbol"] == "ETH/USDT"


def test_unprofitable_signal_records_rejection_without_an_order(tmp_path):
    instance, service = robot(tmp_path)
    now_ms = int(time() * 1000)
    markets = market("LTC/USDT", now_ms)
    markets.books[(Venue.OKX, "LTC/USDT")] = normalize_order_book(
        Venue.OKX,
        "LTC/USDT",
        [["99.90", "20"]],
        [["100.10", "20"]],
        now_ms,
        now_ms,
    )

    result = instance.run_once(markets, now_ms=now_ms)

    state = next(row for row in result["symbol_states"] if row["symbol"] == "LTC/USDT")
    assert state["state"] == "scanning"
    assert state["reason_code"] == "insufficient_edge_after_costs"
    assert service.store.list_orders() == []
    decisions = service.store.list_audit(event_type="crypto_paper_robot_decision")
    assert any(row["symbol"] == "LTC/USDT" and row["decision"] == "rejected" for row in decisions)


def test_restart_replays_same_snapshot_idempotently(tmp_path):
    first, service = robot(tmp_path)
    now_ms = int(time() * 1000)
    markets = market("BTC/USDT", now_ms)
    assert first.run_once(markets, now_ms=now_ms)["state"] == "filled"

    restarted = CryptoPaperRobot(first.settings, service, PersistentAuditLog(service.store))
    assert restarted.run_once(markets, now_ms=now_ms)["state"] == "filled"
    assert len(service.store.list_orders()) == 1
    assert len(service.store.list_fills()) == 2


def test_reconciliation_mismatch_blocks_every_symbol(tmp_path):
    instance, service = robot(tmp_path)
    now_ms = int(time() * 1000)
    service.store.upsert_position(
        {
            "id": "crypto-trial:unexpected",
            "account_id": "crypto-trial",
            "asset": "BTC",
            "symbol": "BTC/USDT",
            "quantity": Decimal("999"),
            "cost_basis": None,
            "mark_price": None,
        }
    )

    result = instance.run_once(market("BTC/USDT", now_ms), now_ms=now_ms)

    assert result["state"] == "halted"
    assert result["reason_code"] == "reconciliation_mismatch"
    assert service.store.list_orders() == []
    assert all(row["state"] == "halted" for row in result["symbol_states"])


def test_live_configuration_halts_without_touching_account(tmp_path):
    instance, service = robot(tmp_path, live_trading_enabled=True)
    now_ms = int(time() * 1000)

    result = instance.run_once(market("BTC/USDT", now_ms), now_ms=now_ms)

    assert result["state"] == "halted"
    assert result["reason_code"] == "live_trading_locked"
    assert service.store.list_orders() == service.store.list_fills() == []
