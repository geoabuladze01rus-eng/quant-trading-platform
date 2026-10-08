import pytest

from quant_trading_platform.signal_watch.intelligence import evaluate
from quant_trading_platform.signal_watch.journal import Candidate, Journal


def test_rejected_candidate_survives_reopen_with_exact_domain_values(tmp_path):
    path = tmp_path / "journal.sqlite"
    result = evaluate((), now_ms=1000, data_hub_quality="insufficient")
    candidate = Candidate("BTC/USDT", "Momentum", result, "range", 1000)
    with Journal(path) as journal:
        signal_id = journal.record(candidate)
        assert journal.record(candidate) == signal_id
    with Journal(path) as journal:
        rows = journal.candidates()
    assert len(rows) == 1
    assert rows[0]["asset"] == "BTC/USDT"
    assert rows[0]["setup"] == "Momentum"
    assert rows[0]["rejected_reason"] == "insufficient_independent_evidence"
    assert rows[0]["score"] == "0"
    assert rows[0]["domains"] == dict.fromkeys("ABCDE", "0")
    assert rows[0]["market_regime"] == "range"
    assert rows[0]["timestamp_ms"] == 1000


def test_signal_identity_includes_score_and_regime(tmp_path):
    result = evaluate((), now_ms=1000, data_hub_quality="insufficient")
    with Journal(tmp_path / "journal.sqlite") as journal:
        first = journal.record(Candidate("ETH/USDT", "Liquidity Sweep", result, "range", 1000))
        second = journal.record(Candidate("ETH/USDT", "Liquidity Sweep", result, "trend", 1000))
    assert first != second


@pytest.mark.parametrize(("asset", "timestamp"), [("LTC/USDT", 1000), ("BTC/USDT", -1)])
def test_invalid_candidate_rejected(asset, timestamp):
    with pytest.raises(ValueError):
        Candidate(
            asset,
            "Momentum",
            evaluate((), now_ms=1000, data_hub_quality="insufficient"),
            "range",
            timestamp,
        )


def test_signal_identity_preserves_independent_origins(tmp_path):
    from test_signal_intelligence import NOW, facts

    from quant_trading_platform.signal_watch.intelligence import Observation

    candidates = []
    for origin in ("binance_flow", "bybit_flow"):
        observations = tuple(
            Observation(
                f.source,
                f.domain,
                f.strength,
                f.timestamp_ms,
                origin if f.domain == "D" else f.origin,
            )
            for f in facts()
        )
        candidates.append(
            Candidate(
                "BTC/USDT",
                "Momentum",
                evaluate(observations, now_ms=NOW, data_hub_quality="healthy"),
                "trend",
                NOW,
            )
        )
    assert candidates[0].signal_id != candidates[1].signal_id
    with Journal(tmp_path / "journal.sqlite") as journal:
        for candidate in candidates:
            journal.record(candidate)
        rows = journal.candidates()
    assert rows[0]["evidence"] != rows[1]["evidence"]
    assert any(row["origin"] == "binance_flow" for row in rows[0]["evidence"])
