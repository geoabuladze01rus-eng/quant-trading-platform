from decimal import Decimal

import pytest

from quant_trading_platform.signal_watch.intelligence import (
    Observation,
    evaluate,
)

D = Decimal
NOW = 1_000_000


def facts(strength: str = "1") -> tuple[Observation, ...]:
    return tuple(
        Observation(
            "Market Structure" if domain != "D" else "Data Hub",
            domain,
            D(strength),
            NOW,
            "price" if domain != "D" else "flow",
        )
        for domain in "ABCDE"
    )


@pytest.mark.parametrize(
    ("strength", "score", "label"),
    [
        ("0.71", "71.00", "REJECTED"),
        ("0.72", "72.00", "HIGH"),
        ("0.83", "83.00", "HIGH"),
        ("0.84", "84.00", "VERY HIGH"),
    ],
)
def test_exact_domain_weights_and_thresholds(strength: str, score: str, label: str) -> None:
    result = evaluate(facts(strength), now_ms=NOW, data_hub_quality="healthy")
    assert result.score == D(score)
    assert result.confidence == label
    assert result.domains == dict(
        zip("ABCDE", map(D, [str(D(strength) * n) for n in (25, 20, 20, 20, 15)]), strict=True)
    )
    assert result.paper_only and not result.live_execution


def test_duplicates_do_not_raise_confidence() -> None:
    observations = facts("0.72")
    assert evaluate(observations * 5, now_ms=NOW, data_hub_quality="healthy").score == D("72")


@pytest.mark.parametrize("quality", ["insufficient", "fail-closed", "unknown", "degraded"])
def test_unusable_data_hub_cannot_confirm_flow(quality: str) -> None:
    result = evaluate(facts(), now_ms=NOW, data_hub_quality=quality)
    assert result.confidence == "REJECTED"
    assert result.rejected_reason == "insufficient_independent_evidence"
    assert result.domains["D"] == 0


def test_degraded_quality_requires_explicit_acceptance() -> None:
    assert (
        evaluate(facts(), now_ms=NOW, data_hub_quality="degraded", allow_degraded=True).confidence
        == "VERY HIGH"
    )


def test_stale_future_and_single_origin_fail_closed() -> None:
    for timestamp in (NOW - 60_001, NOW + 1):
        stale = tuple(
            Observation(f.source, f.domain, f.strength, timestamp, f.origin) for f in facts()
        )
        assert evaluate(stale, now_ms=NOW, data_hub_quality="healthy").confidence == "REJECTED"
    correlated = tuple(Observation(f.source, f.domain, f.strength, NOW, "same") for f in facts())
    assert evaluate(correlated, now_ms=NOW, data_hub_quality="healthy").confidence == "REJECTED"


@pytest.mark.parametrize("strength", [D("NaN"), D("Infinity"), D("-0.1"), D("1.1"), 0.9])
def test_invalid_strength_is_rejected(strength: Decimal) -> None:
    with pytest.raises(ValueError):
        Observation("Gina", "A", strength, NOW, "origin")


def test_missing_trigger_rejects_even_when_score_is_high() -> None:
    result = evaluate(
        tuple(f for f in facts() if f.domain != "C"), now_ms=NOW, data_hub_quality="healthy"
    )
    assert result.score == 80
    assert result.rejected_reason == "missing_core_confirmation"


def test_all_approved_providers_are_accepted_with_explicit_provenance() -> None:
    for source in (
        "Market Structure",
        "TraderSpy",
        "Data Hub",
        "CryptoAudit",
        "TradingCursor",
        "Gina",
        "Exa",
        "Blockscout",
    ):
        assert Observation(source, "E", D("0.5"), NOW, "origin").source == source
    with pytest.raises(ValueError):
        Observation("fabricated provider", "E", D("1"), NOW, "origin")


def test_noncontributing_origin_cannot_supply_independent_confirmation() -> None:
    observations = tuple(
        Observation("Market Structure", domain, D("1"), NOW, "same") for domain in "ABCDE"
    )
    observations += (Observation("Gina", "E", D("0.01"), NOW, "independent"),)
    result = evaluate(observations, now_ms=NOW, data_hub_quality="healthy")
    assert result.confidence == "REJECTED"
    assert result.rejected_reason == "insufficient_independent_evidence"


def test_same_hyperliquid_market_tapes_do_not_count_as_two_independent_origins():
    observations = [
        Observation("Gina", domain, D(1), NOW, "hyperliquid_canonical_usdc_candles")
        for domain in "ABC"
    ]
    observations.append(Observation("Gina", "D", D(1), NOW, "hyperliquid_canonical_usdc_depth"))
    result = evaluate(observations, now_ms=NOW, data_hub_quality="healthy")
    assert result.rejected_reason == "insufficient_independent_evidence"


def test_hyperliquid_book_confirmation_expires_after_five_seconds_at_scoring():
    observations = [
        Observation("Market Structure", domain, D(1), NOW, "okx_completed_candles")
        for domain in "ABC"
    ]
    observations.append(
        Observation("Gina", "D", D(1), NOW - 5_001, "hyperliquid_canonical_usdc_depth")
    )
    result = evaluate(observations, now_ms=NOW, data_hub_quality="healthy")
    assert result.domains["D"] == 0
    assert "stale_or_future:Gina" in result.warnings
