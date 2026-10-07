import copy

import httpx
import pytest

from quant_trading_platform.mcp.evidence_client import EvidenceClient, EvidenceUnavailable


def payload():
    return {
        "symbol": "BTC/USDT",
        "generated_at_ms": 1000,
        "spot": {"venues": [], "cross_venue_spread": None},
        "derivatives": {
            "venues": [],
            "funding_consensus": None,
            "open_interest_change": None,
            "mark_spot_basis": [],
        },
        "liquidations": {
            name: {"venues": [], "comparable": False, "cross_venue_notional": None}
            for name in ("window_5m", "window_15m", "window_1h")
        },
        "quality": {
            "status": "insufficient",
            "fresh_sources": 0,
            "expected_sources": 6,
            "missing": [
                f"{kind}:{venue}"
                for venue in ("binance", "bybit", "okx")
                for kind in ("spot", "derivatives")
            ],
            "stale": [],
            "warnings": [],
        },
        "execution": {"paper_only": True, "live_execution": False},
    }


@pytest.mark.asyncio
async def test_exact_read_only_request_drops_inherited_credentials_and_unknown_fields():
    def handler(request):
        assert str(request.url) == "http://127.0.0.1:8000/signal-evidence/BTC%2FUSDT"
        assert request.method == "GET"
        assert request.extensions["timeout"]["read"] == 5
        assert not set(request.headers) & {"authorization", "cookie", "x-mbx-apikey"}
        value = payload()
        value["raw_headers"] = {"secret": "hidden"}
        value["spot"]["raw_auth"] = "hidden"
        return httpx.Response(200, json=value)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=("a", "secret"),
        cookies={"secret": "cookie"},
        headers={"X-MBX-APIKEY": "secret"},
    ) as client:
        adapter = EvidenceClient(client=client, clock=lambda: 1001)
        result = await adapter.get_signal_evidence("BTC/USDT")
        assert result == payload()
        await adapter.close()
        assert not client.is_closed


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://u:p@example.com",
        "http://127.0.0.1:8000/paper",
        "https://example.com?q=1",
        "https://example.com#x",
        "file:///tmp",
        "http://localhost.evil",
    ],
)
def test_invalid_origin_rejected(origin):
    with pytest.raises(ValueError):
        EvidenceClient(origin=origin)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "symbol",
        "future",
        "stale",
        "live",
        "paper",
        "float",
        "malformed",
        "oversize",
        "http",
        "redirect",
        "quality",
    ],
)
async def test_upstream_failures_are_sanitized_and_fail_closed(fault):
    def handler(request):
        value = copy.deepcopy(payload())
        if fault == "symbol":
            value["symbol"] = "ETH/USDT"
        if fault == "future":
            value["generated_at_ms"] = 1002
        if fault == "stale":
            value["generated_at_ms"] = 1
        if fault == "live":
            value["execution"]["live_execution"] = True
        if fault == "paper":
            value["execution"]["paper_only"] = False
        if fault == "float":
            value["derivatives"]["funding_consensus"] = {"median": 0.01}
        if fault == "quality":
            value["quality"]["status"] = "healthy"
        if fault == "malformed":
            return httpx.Response(200, text="secret malformed upstream")
        if fault == "oversize":
            return httpx.Response(200, content=b"secret" * 50000)
        if fault == "http":
            return httpx.Response(500, text="secret")
        if fault == "redirect":
            return httpx.Response(302, headers={"Location": "https://secret"})
        return httpx.Response(200, json=value)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = EvidenceClient(client=client, clock=lambda: 6002 if fault == "stale" else 1001)
        with pytest.raises(EvidenceUnavailable) as error:
            await adapter.get_signal_evidence("BTC/USDT")
        assert str(error.value) == "signal_evidence_unavailable"
        assert error.value.__cause__ is None


@pytest.mark.asyncio
async def test_symbols_cannot_select_arbitrary_path_or_url():
    def forbidden(request):
        pytest.fail("invalid symbol made upstream request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        adapter = EvidenceClient(client=client)
        for symbol in ("LTC/USDT", "BTCUSDT", "../paper/orders", "https://evil"):
            with pytest.raises(ValueError):
                await adapter.get_signal_evidence(symbol)


def populated_payload(spot_count=3, derivative_count=3):
    value = payload()
    venues = ["binance", "bybit", "okx"]
    value["spot"]["venues"] = [
        {
            "venue": v,
            "bid": "99",
            "ask": "101",
            "timestamp_ms": 1000,
            "received_at_ms": 1000,
            "timestamp_source": "exchange",
            "depth_imbalance": "0",
            "contributing_venues": [v],
        }
        for v in venues[:spot_count]
    ]
    value["spot"]["cross_venue_spread"] = {
        "midpoint_range_pct": "0",
        "contributing_venues": venues[:spot_count],
    }
    value["derivatives"]["venues"] = [
        {
            "venue": v,
            "symbol": "BTC/USDT",
            "instrument_id": "BTC-USDT-SWAP" if v == "okx" else "BTCUSDT",
            "timestamp_ms": 1000,
            "received_at_ms": 1000,
            "mark_price": "100",
            "index_price": "100",
            "funding_rate": None,
            "next_funding_time_ms": None,
            "open_interest": "2",
            "open_interest_unit": "contracts" if v == "okx" else "base_asset",
            "source_fields": ["public_snapshot"],
            "mark_spot_basis_pct": "0",
            "index_mark_deviation_pct": "0",
            "contributing_venues": [v],
        }
        for v in venues[:derivative_count]
    ]
    value["derivatives"]["mark_spot_basis"] = [
        {"venue": v, "basis_pct": "0", "contributing_venues": [v]}
        for v in venues[:derivative_count]
    ]
    missing = [
        f"{kind}:{v}"
        for v in venues
        for kind, n in (("spot", spot_count), ("derivatives", derivative_count))
        if v not in venues[:n]
    ]
    value["quality"].update(
        status="healthy" if not missing else "degraded",
        fresh_sources=spot_count + derivative_count,
        missing=missing,
    )
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("counts", [(3, 3), (2, 2)])
async def test_complete_healthy_and_degraded_contract_accepted(counts):
    value = populated_payload(*counts)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=value))
    ) as client:
        result = await EvidenceClient(client=client, clock=lambda: 1001).get_signal_evidence(
            "BTC/USDT"
        )
        assert result == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "healthy_missing",
        "missing_price",
        "stale_spot",
        "stale_derivatives",
        "future_source",
        "instrument",
        "missing_windows",
        "bid_dict",
        "bid_list",
        "missing_labels",
        "wrong_completeness",
        "wrong_unit",
    ],
)
async def test_untrusted_populated_evidence_cannot_be_healthy(fault):
    value = populated_payload(2, 2) if fault == "healthy_missing" else populated_payload()
    if fault == "healthy_missing":
        value["quality"]["status"] = "healthy"
    if fault == "missing_price":
        del value["spot"]["venues"][0]["bid"]
    if fault == "stale_spot":
        value["spot"]["venues"][0]["timestamp_ms"] = 1
    if fault == "stale_derivatives":
        value["derivatives"]["venues"][0]["timestamp_ms"] = 1
    if fault == "future_source":
        value["spot"]["venues"][0]["timestamp_ms"] = 9999
    if fault == "instrument":
        value["derivatives"]["venues"][0]["instrument_id"] = "ETHUSDT"
    if fault == "missing_windows":
        value["liquidations"] = {}
    if fault == "bid_dict":
        value["spot"]["venues"][0]["bid"] = {"authorization": "secret"}
    if fault == "bid_list":
        value["spot"]["venues"][0]["bid"] = []
    if fault == "missing_labels":
        value = populated_payload(2, 2)
        value["quality"]["missing"] = []
    if fault == "wrong_completeness":
        value["liquidations"]["window_5m"]["comparable"] = True
    if fault == "wrong_unit":
        value["derivatives"]["venues"][0]["open_interest_unit"] = "contracts"
    now = 400000 if fault == "stale_derivatives" else 2002 if fault == "stale_spot" else 1001
    value["generated_at_ms"] = now
    # Keep the other source class fresh: failures must refer to the mutated class.
    for kind in ("spot", "derivatives"):
        for n, row in enumerate(value[kind]["venues"]):
            if (fault == "stale_spot" and kind == "spot" and n == 0) or (
                fault == "stale_derivatives" and kind == "derivatives" and n == 0
            ):
                continue
            if fault in ("stale_spot", "stale_derivatives"):
                row["timestamp_ms"] = row["received_at_ms"] = now
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=value))
    ) as client:
        with pytest.raises(EvidenceUnavailable):
            await EvidenceClient(client=client, clock=lambda: now).get_signal_evidence("BTC/USDT")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "negative_count", "native_unit", "missing_size", "future_event"]
)
async def test_liquidation_rows_have_valid_native_contract(fault):
    value = populated_payload()
    row = {
        "venue": "binance",
        "event_count": 1,
        "long_quantity": "2",
        "short_quantity": "0",
        "quantity_unit": "base_asset",
        "largest_event_quantity": "2",
        "latest_event_timestamp_ms": 1000,
        "source_completeness": "partial_exchange_stream",
    }
    if fault == "negative_count":
        row["event_count"] = -1
    if fault == "native_unit":
        row["quantity_unit"] = "contracts"
    if fault == "missing_size":
        del row["largest_event_quantity"]
    if fault == "future_event":
        row["latest_event_timestamp_ms"] = 2000
    value["liquidations"]["window_5m"]["venues"] = [row]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=value))
    ) as client:
        adapter = EvidenceClient(client=client, clock=lambda: 1001)
        if fault:
            with pytest.raises(EvidenceUnavailable):
                await adapter.get_signal_evidence("BTC/USDT")
        else:
            result = await adapter.get_signal_evidence("BTC/USDT")
            assert result["liquidations"] == value["liquidations"]
