"""Explainable domain confidence over timestamped, independently sourced evidence."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

WEIGHTS = MappingProxyType(dict(zip("ABCDE", (25, 20, 20, 20, 15), strict=True)))
PROVIDERS = frozenset(
    (
        "Market Structure",
        "TraderSpy",
        "Data Hub",
        "CryptoAudit",
        "TradingCursor",
        "Gina",
        "Exa",
        "Blockscout",
    )
)


def independent_origin(origin: str) -> str:
    if origin in ("hyperliquid_canonical_usdc_candles", "hyperliquid_canonical_usdc_depth"):
        return "hyperliquid_canonical_usdc"
    return origin


@dataclass(frozen=True)
class Observation:
    source: str
    domain: str
    strength: Decimal
    timestamp_ms: int
    origin: str

    def __post_init__(self) -> None:
        if (
            self.source not in PROVIDERS
            or self.domain not in WEIGHTS
            or not isinstance(self.strength, Decimal)
            or not self.strength.is_finite()
            or not Decimal(0) <= self.strength <= Decimal(1)
            or type(self.timestamp_ms) is not int
            or self.timestamp_ms <= 0
            or not self.origin.strip()
        ):
            raise ValueError("Invalid observation/provenance")


def observation_max_age_ms(observation: Observation) -> int:
    return 5_000 if observation.origin == "hyperliquid_canonical_usdc_depth" else 60_000


@dataclass(frozen=True)
class Confidence:
    score: Decimal
    domains: Mapping[str, Decimal]
    confidence: str
    rejected_reason: str | None
    accepted_sources: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence: tuple[Observation, ...] = ()
    paper_only: bool = True
    live_execution: bool = False


def evaluate(
    observations: Sequence[Observation],
    *,
    now_ms: int,
    data_hub_quality: str,
    max_age_ms: int = 60_000,
    allow_degraded: bool = False,
) -> Confidence:
    if type(now_ms) is not int or now_ms <= 0 or max_age_ms <= 0:
        raise ValueError("Invalid evidence clock/freshness limit")
    domains = dict.fromkeys(WEIGHTS, Decimal(0))
    sources: set[str] = set()
    origins: set[str] = set()
    contributors: dict[str, set[Observation]] = {domain: set() for domain in WEIGHTS}
    warnings: set[str] = set()
    hub_usable = data_hub_quality == "healthy" or (
        data_hub_quality == "degraded" and allow_degraded
    )
    for observation in observations:
        if not 0 <= now_ms - observation.timestamp_ms <= min(
            max_age_ms, observation_max_age_ms(observation)
        ):
            warnings.add("stale_or_future:" + observation.source)
            continue
        if observation.source == "Data Hub" and not hub_usable:
            warnings.add("unusable_data_hub")
            continue
        if observation.strength == 0:
            continue
        points = observation.strength * WEIGHTS[observation.domain]
        if points > domains[observation.domain]:
            domains[observation.domain] = points
            contributors[observation.domain] = {observation}
        elif points == domains[observation.domain]:
            contributors[observation.domain].add(observation)
    evidence: set[Observation] = set()
    for contributions in contributors.values():
        for contribution in contributions:
            sources.add(contribution.source)
            origins.add(independent_origin(contribution.origin))
            evidence.add(contribution)
    score = sum(domains.values(), Decimal(0))
    reason = None
    if len(origins) < 2:
        reason = "insufficient_independent_evidence"
    elif any(domains[domain] == 0 for domain in "ABC"):
        reason = "missing_core_confirmation"
    elif score < 72:
        reason = "below_confidence_threshold"
    label = "REJECTED" if reason else ("VERY HIGH" if score >= 84 else "HIGH")
    return Confidence(
        score,
        MappingProxyType(domains),
        label,
        reason,
        tuple(sorted(sources)),
        tuple(sorted(warnings)),
        tuple(
            sorted(
                evidence,
                key=lambda item: (
                    item.domain,
                    item.source,
                    item.origin,
                    item.timestamp_ms,
                    item.strength,
                ),
            )
        ),
    )
