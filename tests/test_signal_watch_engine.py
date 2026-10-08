from decimal import Decimal as D

import pytest
from test_signal_intelligence import NOW, facts

from quant_trading_platform.signal_watch.engine import Frame, WatchEngine, detect_setups
from quant_trading_platform.signal_watch.journal import Journal


def frame(**overrides):
    values = dict(
        timestamp_ms=NOW,
        close=D("102"),
        previous_close=D("101"),
        high=D("103"),
        low=D("99"),
        support=D("100"),
        resistance=D("101"),
        trend=D("1"),
        volume_ratio=D("2"),
        momentum=D("0.02"),
        previous_high=D("102"),
        previous_low=D("98"),
    )
    return Frame(**(values | overrides))


@pytest.mark.parametrize(
    ("setup", "inputs"),
    [
        ("Trend Pullback", dict(low=D("99.9"), resistance=D("110"), momentum=D("0.001"))),
        ("Breakout + Retest", dict(low=D("101"), previous_close=D("102"))),
        ("Liquidity Sweep", dict(low=D("98"), previous_low=D("99"))),
        ("Momentum", {}),
    ],
)
def test_detects_four_setups(setup, inputs):
    assert setup in detect_setups(frame(**inputs))


def test_invalid_or_stale_structure_fails_closed():
    with pytest.raises(ValueError):
        frame(close=D("NaN"))
    with pytest.raises(ValueError):
        frame(low=D("200"))


def test_every_setup_is_journaled_including_rejections_and_antimiss(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        engine = WatchEngine(journal)
        candidates = engine.scan(
            "BTC/USDT",
            frame(),
            (),
            data_hub_quality="insufficient",
            now_ms=NOW,
            market_regime="trend",
        )
        assert len(candidates) == 4
        assert len(journal.candidates()) == 4
        assert all(c.result.confidence == "REJECTED" for c in candidates)
        assert engine.missed_opportunities()
        assert (
            engine.scan(
                "BTC/USDT",
                frame(timestamp_ms=NOW - 60_001),
                facts(),
                data_hub_quality="healthy",
                now_ms=NOW,
                market_regime="trend",
            )[0].result.rejected_reason
            == "stale_structure"
        )


def test_setup_absence_cannot_be_bypassed_by_high_score(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        candidates = WatchEngine(journal).scan(
            "SOL/USDT",
            frame(
                trend=D("0"),
                low=D("100"),
                previous_low=D("99"),
                resistance=D("110"),
                momentum=D("0"),
                volume_ratio=D("1"),
            ),
            facts(),
            data_hub_quality="healthy",
            now_ms=NOW,
            market_regime="range",
        )
    assert all(c.result.rejected_reason == "setup_not_confirmed" for c in candidates)


def test_calibration_uses_observed_outcomes_without_changing_thresholds(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        engine = WatchEngine(journal)
        candidates = engine.scan(
            "ETH/USDT",
            frame(),
            facts(),
            data_hub_quality="healthy",
            now_ms=NOW,
            market_regime="trend",
        )
        selected = next(c for c in candidates if c.setup == "Momentum")
        engine.record_outcome(selected.signal_id, net_return=D("0.01"), timestamp_ms=NOW + 1000)
        engine.record_outcome(selected.signal_id, net_return=D("0.01"), timestamp_ms=NOW + 1000)
        assert engine.calibration()["VERY HIGH"] == {
            "samples": 1,
            "positive": 1,
            "mean_net_return": "0.01",
        }
        with pytest.raises(ValueError):
            engine.record_outcome("unknown", net_return=D("1"), timestamp_ms=NOW + 1000)
        assert (
            len(
                engine.scan(
                    "ETH/USDT",
                    frame(),
                    facts(),
                    data_hub_quality="healthy",
                    now_ms=NOW,
                    market_regime="trend",
                )
            )
            == 4
        )


def test_antimiss_never_promotes_rejected_candidate(tmp_path):
    with Journal(tmp_path / "journal.sqlite") as journal:
        engine = WatchEngine(journal)
        candidates = engine.scan(
            "BTC/USDT",
            frame(),
            facts("0.71"),
            data_hub_quality="healthy",
            now_ms=NOW,
            market_regime="trend",
        )
        assert all(c.result.confidence == "REJECTED" for c in candidates)
        missed = engine.missed_opportunities()
        assert any(r["reason"] == "near_threshold" for r in missed)
        engine.scan(
            "BTC/USDT",
            frame(),
            facts("0.71"),
            data_hub_quality="healthy",
            now_ms=NOW,
            market_regime="trend",
        )
        assert len(engine.missed_opportunities()) == len(missed)


def test_deep_support_break_is_not_a_trend_pullback():
    assert "Trend Pullback" not in detect_setups(frame(low=D("50"), support=D("100")))
