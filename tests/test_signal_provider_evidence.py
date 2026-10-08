import json
from decimal import Decimal as D

import pytest

from quant_trading_platform.signal_watch.providers import ProviderEvidenceCache

NOW = 1_000_000


def report(symbol="BTC/USDT", timestamp=NOW, origin="exchange_observation"):
    return {
        "symbol": symbol,
        "observations": [
            {"domain": "E", "strength": "0.8", "timestamp_ms": timestamp, "origin": origin}
        ],
    }


def test_all_providers_can_supply_exact_timestamped_independent_evidence():
    cache = ProviderEvidenceCache()
    for source in ("TraderSpy", "CryptoAudit", "TradingCursor", "Gina", "Exa", "Blockscout"):
        assert cache.update(source, report(origin=source + "_verified"), now_ms=NOW)
    values = cache.observations("BTC/USDT", now_ms=NOW)
    assert len(values) == 6
    assert all(v.strength == D("0.8") for v in values)


def test_one_invalid_provider_is_cleared_without_invalidating_others():
    cache = ProviderEvidenceCache()
    cache.update("Gina", report(), now_ms=NOW)
    cache.update("TraderSpy", report(), now_ms=NOW)
    assert not cache.update(
        "Gina", {"symbol": "BTC/USDT", "observations": [{"strength": "1"}]}, now_ms=NOW
    )
    assert [o.source for o in cache.observations("BTC/USDT", now_ms=NOW)] == ["TraderSpy"]
    assert cache.status("Gina", "BTC/USDT", now_ms=NOW) == "error"


def test_stale_future_and_wrong_symbol_are_never_relabelled():
    cache = ProviderEvidenceCache()
    assert not cache.update("Gina", report(timestamp=NOW + 1), now_ms=NOW)
    assert cache.update("Gina", report("ETH/USDT"), now_ms=NOW)
    assert cache.observations("BTC/USDT", now_ms=NOW) == ()
    assert cache.observations("ETH/USDT", now_ms=NOW + 60_001) == ()
    assert cache.status("Gina", "ETH/USDT", now_ms=NOW + 60_001) == "stale"


def test_raw_json_numbers_parse_as_decimal_but_python_floats_are_rejected():
    cache = ProviderEvidenceCache()
    payload = report()
    payload["observations"][0]["strength"] = 0.8
    assert cache.update("Gina", json.dumps(payload), now_ms=NOW)
    assert cache.observations("BTC/USDT", now_ms=NOW)[0].strength == D("0.8")
    assert not cache.update("Gina", payload, now_ms=NOW)


def test_current_cryptoaudit_response_without_timestamp_is_not_confirmatory():
    cache = ProviderEvidenceCache()
    payload = {
        "success": True,
        "symbol": "BTCUSDT",
        "trend": {"direction": "bullish", "strength": 85},
    }
    assert not cache.update("CryptoAudit", payload, now_ms=NOW)
    assert cache.observations("BTC/USDT", now_ms=NOW) == ()


def test_duplicate_domains_or_unknown_provider_fail_closed():
    cache = ProviderEvidenceCache()
    payload = report()
    payload["observations"] *= 2
    assert not cache.update("Gina", payload, now_ms=NOW)
    with pytest.raises(ValueError):
        cache.update("invented", report(), now_ms=NOW)


def test_cached_provider_statuses_are_read_only_and_report_missing_sources():
    cache = ProviderEvidenceCache()
    cache.update("Gina", report(), now_ms=NOW)
    status = cache.snapshot(now_ms=NOW)
    assert any(
        r["source"] == "Gina" and r["symbol"] == "BTC/USDT" and r["status"] == "ok" for r in status
    )
    assert any(r["source"] == "TraderSpy" and r["status"] == "no_data" for r in status)
    assert cache.snapshot(now_ms=NOW) == status


def test_concurrent_invalidation_cannot_break_read_only_quality_snapshot():
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    cache = ProviderEvidenceCache()
    cache.update("TraderSpy", report(), now_ms=NOW)
    started, stop = Event(), Event()
    previous_interval = sys.getswitchinterval()

    def mutate():
        started.set()
        while not stop.is_set():
            cache.update("Gina", report(), now_ms=NOW)
            cache.update("Gina", {"symbol": "BTC/USDT", "observations": []}, now_ms=NOW)

    sys.setswitchinterval(0.000001)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(mutate)
            started.wait()
            try:
                for _ in range(1000):
                    rows = cache.snapshot(now_ms=NOW)
                    assert (
                        next(
                            r["status"]
                            for r in rows
                            if r["source"] == "TraderSpy" and r["symbol"] == "BTC/USDT"
                        )
                        == "ok"
                    )
                    assert any(
                        o.source == "TraderSpy" for o in cache.observations("BTC/USDT", now_ms=NOW)
                    )
            finally:
                stop.set()
                writer.result(timeout=5)
    finally:
        sys.setswitchinterval(previous_interval)


def test_gina_book_cache_expires_at_source_freshness_before_generic_report_limit():
    cache = ProviderEvidenceCache()
    payload = report(origin="hyperliquid_canonical_usdc_depth")
    payload["observations"][0]["domain"] = "D"
    assert cache.update("Gina", payload, now_ms=NOW)
    assert cache.status("Gina", "BTC/USDT", now_ms=NOW + 5_000) == "ok"
    assert cache.status("Gina", "BTC/USDT", now_ms=NOW + 5_001) == "stale"
    assert not cache.observations("BTC/USDT", now_ms=NOW + 5_001)
