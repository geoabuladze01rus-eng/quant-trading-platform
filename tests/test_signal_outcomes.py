from decimal import Decimal as D

import pytest
from test_signal_intelligence import NOW, facts
from test_signal_watch_engine import frame

from quant_trading_platform.signal_watch.engine import WatchEngine
from quant_trading_platform.signal_watch.journal import Journal
from quant_trading_platform.signal_watch.outcomes import OutcomeTracker


def test_forward_markout_waits_for_exact_horizon_and_survives_restart(tmp_path):
    path = tmp_path / "journal.sqlite"
    with Journal(path) as journal:
        engine = WatchEngine(journal)
        candidate = next(
            c
            for c in engine.scan(
                "BTC/USDT",
                frame(),
                facts(),
                data_hub_quality="healthy",
                now_ms=NOW,
                market_regime="trend",
            )
            if c.setup == "Momentum"
        )
        tracker = OutcomeTracker(
            engine, horizon_ms=900_000, fee_rate=D("0.001"), slippage_rate=D("0.0005")
        )
        tracker.register(candidate, entry_price=D("100"))
        tracker.observe(
            "BTC/USDT", price=D("110"), timestamp_ms=NOW + 899_999, now_ms=NOW + 899_999
        )
        assert tracker.calibration()["VERY HIGH"]["samples"] == 0
    with Journal(path) as journal:
        engine = WatchEngine(journal)
        tracker = OutcomeTracker(
            engine, horizon_ms=900_000, fee_rate=D("0.001"), slippage_rate=D("0.0005")
        )
        tracker.observe(
            "BTC/USDT", price=D("110"), timestamp_ms=NOW + 900_000, now_ms=NOW + 900_000
        )
        tracker.observe(
            "BTC/USDT", price=D("110"), timestamp_ms=NOW + 900_000, now_ms=NOW + 900_000
        )
        expected = D("110") * (1 - D("0.0015")) / (D("100") * (1 + D("0.0015"))) - 1
        assert tracker.calibration()["VERY HIGH"]["samples"] == 1
        assert D(tracker.calibration()["VERY HIGH"]["mean_net_return"]) == expected
        assert tracker.snapshot()[0]["kind"] == "estimated_forward_markout"
        assert tracker.snapshot()[0]["status"] == "measured"


def test_future_stale_and_other_asset_prices_cannot_resolve_outcome(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        engine = WatchEngine(journal)
        candidate = next(
            c
            for c in engine.scan(
                "BTC/USDT",
                frame(),
                facts(),
                data_hub_quality="healthy",
                now_ms=NOW,
                market_regime="trend",
            )
            if c.setup == "Momentum"
        )
        tracker = OutcomeTracker(engine)
        tracker.register(candidate, entry_price=D("100"))
        for symbol, timestamp, now in [
            ("ETH/USDT", NOW + 900_000, NOW + 900_000),
            ("BTC/USDT", NOW + 900_000, NOW + 899_999),
            ("BTC/USDT", NOW + 900_000, NOW + 960_001),
        ]:
            tracker.observe(symbol, price=D("110"), timestamp_ms=timestamp, now_ms=now)
        assert tracker.calibration()["VERY HIGH"]["samples"] == 0


def test_missed_exact_horizon_is_diagnostic_not_fabricated_return(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        engine = WatchEngine(journal)
        candidate = next(
            c
            for c in engine.scan(
                "BTC/USDT",
                frame(),
                facts(),
                data_hub_quality="healthy",
                now_ms=NOW,
                market_regime="trend",
            )
            if c.setup == "Momentum"
        )
        tracker = OutcomeTracker(engine)
        tracker.register(candidate, entry_price=D("100"))
        tracker.observe(
            "BTC/USDT", price=D("110"), timestamp_ms=NOW + 960_000, now_ms=NOW + 960_000
        )
        assert tracker.snapshot()[0]["status"] == "missed_horizon"
        assert tracker.calibration()["VERY HIGH"]["samples"] == 0


@pytest.mark.parametrize("price", [D("NaN"), D("-1"), 100.0])
def test_invalid_entry_fails_closed(tmp_path, price):
    with Journal(tmp_path / "journal.sqlite") as journal:
        engine = WatchEngine(journal)
        candidate = engine.scan(
            "BTC/USDT",
            frame(),
            facts(),
            data_hub_quality="healthy",
            now_ms=NOW,
            market_regime="trend",
        )[0]
        with pytest.raises(ValueError):
            OutcomeTracker(engine).register(candidate, entry_price=price)


def test_manual_outcomes_are_not_mixed_into_estimated_markout_statistics(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        engine = WatchEngine(journal)
        candidate = next(
            c
            for c in engine.scan(
                "BTC/USDT",
                frame(),
                facts(),
                data_hub_quality="healthy",
                now_ms=NOW,
                market_regime="trend",
            )
            if c.setup == "Momentum"
        )
        engine.record_outcome(candidate.signal_id, net_return=D("0.9"), timestamp_ms=NOW + 1)
        tracker = OutcomeTracker(engine)
        assert tracker.calibration()["VERY HIGH"]["samples"] == 0


def test_measured_markout_does_not_block_later_observed_outcome(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        engine = WatchEngine(journal)
        candidate = next(
            c
            for c in engine.scan(
                "BTC/USDT",
                frame(),
                facts(),
                data_hub_quality="healthy",
                now_ms=NOW,
                market_regime="trend",
            )
            if c.setup == "Momentum"
        )
        tracker = OutcomeTracker(engine)
        tracker.register(candidate, entry_price=D("100"))
        tracker.observe(
            "BTC/USDT", price=D("110"), timestamp_ms=NOW + 900_000, now_ms=NOW + 900_000
        )
        engine.record_outcome(candidate.signal_id, net_return=D("0.9"), timestamp_ms=NOW + 900_001)
        assert engine.calibration()["VERY HIGH"]["mean_net_return"] == "0.9"
        assert tracker.calibration()["VERY HIGH"]["mean_net_return"] != "0.9"
