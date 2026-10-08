from importlib import import_module
from types import SimpleNamespace

import httpx
import pytest

from quant_trading_platform.market_data.derivatives import (
    DerivativesEvidenceService,
    MultiDerivativesEvidenceService,
    normalize_derivatives_snapshot,
)
from quant_trading_platform.market_data.liquidations import LiquidationWindow
from quant_trading_platform.market_data.models import normalize_order_book
from quant_trading_platform.market_data.service import MarketDataService, MultiMarketDataService
from quant_trading_platform.market_data.signal_evidence import get_signal_evidence
from quant_trading_platform.models import Venue

VENUES = (Venue.BINANCE, Venue.BYBIT, Venue.OKX)


class SpotSource:
    def __init__(self, venue):
        self.venue = venue

    def get_order_book(self, symbol):
        return normalize_order_book(self.venue, symbol, [["99", "2"]], [["101", "1"]], 1000, 1000)

    def close(self):
        pass


class DerivativeSource:
    def __init__(self, venue):
        self.venue = venue

    def get_snapshot(self, symbol):
        return normalize_derivatives_snapshot(
            venue=self.venue,
            symbol=symbol,
            instrument_id="BTC-USDT-SWAP" if self.venue == Venue.OKX else "BTCUSDT",
            timestamp_ms=1000,
            received_at_ms=1000,
            mark_price="102",
            index_price="101",
            funding_rate=".001",
            open_interest="3",
            open_interest_unit="contracts" if self.venue == Venue.OKX else "base_asset",
        )

    def close(self):
        pass


async def services(spot_count, derivative_count):
    spot = MarketDataService([SpotSource(v) for v in VENUES[:spot_count]], {}, clock=lambda: 1000)
    derivative = DerivativesEvidenceService(
        [DerivativeSource(v) for v in VENUES[:derivative_count]], clock=lambda: 1000
    )
    await spot.poll_once()
    await derivative.poll_once()
    return MultiMarketDataService([spot]), MultiDerivativesEvidenceService([derivative])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spot_n,deriv_n,status",
    [(3, 3, "healthy"), (2, 2, "degraded"), (3, 1, "insufficient"), (1, 3, "insufficient")],
)
async def test_quality_units_and_metrics(spot_n, deriv_n, status):
    spot, derivatives = await services(spot_n, deriv_n)
    result = get_signal_evidence(
        "BTC/USDT",
        spot=spot,
        derivatives=derivatives,
        liquidations=LiquidationWindow(),
        generated_at_ms=1000,
    )
    assert result["quality"]["status"] == status
    assert result["quality"]["expected_sources"] == 6
    assert result["quality"]["fresh_sources"] == spot_n + deriv_n
    assert result["execution"] == {"paper_only": True, "live_execution": False}
    assert result["derivatives"]["open_interest_change"] is None
    assert "open_interest_total" not in result["derivatives"]
    assert result["derivatives"]["venues"][0]["mark_spot_basis_pct"] == ("2.00" if spot_n else None)
    assert result["derivatives"]["funding_consensus"]["median"] == "0.001"
    if deriv_n == 3:
        assert result["derivatives"]["venues"][2]["open_interest_unit"] == "contracts"
    import json

    serialized = json.dumps(result)
    for directive in ('"BUY"', '"SELL"', '"LONG"', '"SHORT"', '"price_target"'):
        assert directive not in serialized


@pytest.mark.asyncio
async def test_stale_class_is_insufficient_even_if_other_class_fresh():
    spot, derivatives = await services(3, 3)
    derivatives.services[0].clock = lambda: 400_000
    result = get_signal_evidence(
        "BTC/USDT",
        spot=spot,
        derivatives=derivatives,
        liquidations=LiquidationWindow(),
        generated_at_ms=1000,
    )
    assert result["quality"]["status"] == "insufficient"
    assert len(result["quality"]["stale"]) == 3
    assert result["derivatives"]["venues"] == []


@pytest.mark.asyncio
async def test_get_only_read_only_contract_and_live_lock(monkeypatch):
    api = import_module("quant_trading_platform.api.app")
    spot, derivatives = await services(3, 3)
    monkeypatch.setattr(api.app.state, "market_data", spot, raising=False)
    monkeypatch.setattr(api.app.state, "derivatives", derivatives, raising=False)
    monkeypatch.setattr(api.app.state, "liquidations", LiquidationWindow(), raising=False)
    monkeypatch.setattr(api, "now_ms", lambda: 1000)
    monkeypatch.setattr(api.app.state, 'liquidation_collectors', [SimpleNamespace(
        venue=Venue.OKX, status='connected', error=None, last_received_at_ms=990,
        heartbeat_seconds=20,
    )], raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("GET evidence performed upstream IO or execution")

    monkeypatch.setattr(
        "quant_trading_platform.connectors.crypto.client.PublicCryptoConnector.place_order",
        forbidden,
    )
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as client:
        response = await client.get("/signal-evidence/BTC%2FUSDT")
        assert response.status_code == 200
        assert response.json()["quality"]["status"] == "healthy"
        assert response.json()['liquidation_sources'] == [{
            'venue': 'okx', 'status': 'connected', 'error': None,
            'last_received_at_ms': 990,
        }]
        assert (await client.post("/signal-evidence/BTC%2FUSDT")).status_code == 405
        assert (await client.get("/signal-evidence/LTC%2FUSDT")).status_code == 422
        assert (await client.get("/health")).json()["live_trading"] == "locked"


@pytest.mark.asyncio
async def test_okx_only_mode_does_not_count_missing_exchanges_as_failures():
    spot = MarketDataService([SpotSource(Venue.OKX)], {}, clock=lambda: 1000)
    derivatives = DerivativesEvidenceService([DerivativeSource(Venue.OKX)], clock=lambda: 1000)
    await spot.poll_once()
    await derivatives.poll_once()
    result = get_signal_evidence(
        "BTC/USDT",
        spot=spot,
        derivatives=derivatives,
        liquidations=LiquidationWindow(),
        generated_at_ms=1000,
        venues=(Venue.OKX,),
    )
    assert result["quality"]["status"] == "healthy"
    assert result["quality"]["expected_sources"] == 2
    assert result["quality"]["fresh_sources"] == 2
    assert result["quality"]["missing"] == []
    assert result["quality"]["stale"] == []
    assert (
        "Single OKX venue: no independent cross-venue corroboration"
        in result["quality"]["warnings"]
    )
    assert [row["venue"] for row in result["spot"]["venues"]] == ["okx"]
    assert [row["venue"] for row in result["derivatives"]["venues"]] == ["okx"]
    assert result["spot"]["cross_venue_spread"] is None


def test_configured_crypto_venues_only_okx_rejects_invalid_inputs(monkeypatch):
    api = import_module("quant_trading_platform.api.app")
    monkeypatch.setattr(api.settings, "crypto_market_venues", "okx")
    assert api.configured_crypto_venues() == (Venue.OKX,)
    for bad in ("", "okx,okx", "okx,bybit,unknown", "binance,unknown"):
        monkeypatch.setattr(api.settings, "crypto_market_venues", bad)
        with pytest.raises(ValueError, match="supported crypto market venues"):
            api.configured_crypto_venues()



@pytest.mark.asyncio
async def test_okx_only_evidence_client_validates_exact_scope_and_fail_closes():
    import copy
    import json

    from quant_trading_platform.mcp.evidence_client import _validate

    spot = MarketDataService([SpotSource(Venue.OKX)], {}, clock=lambda: 1000)
    derivatives = DerivativesEvidenceService(
        [DerivativeSource(Venue.OKX)], clock=lambda: 1000
    )
    await spot.poll_once()
    await derivatives.poll_once()
    packet = json.loads(json.dumps(get_signal_evidence(
        "BTC/USDT", spot=spot, derivatives=derivatives,
        liquidations=LiquidationWindow(), generated_at_ms=1000,
        venues=(Venue.OKX,),
    )))
    # The lightweight derivatives fixture omits production source metadata.
    packet["derivatives"]["venues"][0]["source_fields"] = ["fixture_public_ticker"]
    verified = _validate(packet, "BTC/USDT", 1000)
    assert verified["quality"]["status"] == "healthy"
    assert verified["quality"]["expected_sources"] == 2
    assert [row["venue"] for row in verified["spot"]["venues"]] == ["okx"]

    spoofed = copy.deepcopy(packet)
    spoofed["spot"]["venues"][0]["venue"] = "bybit"
    with pytest.raises(ValueError):
        _validate(spoofed, "BTC/USDT", 1000)

    spoofed = copy.deepcopy(packet)
    spoofed["quality"]["expected_sources"] = 6
    with pytest.raises(ValueError):
        _validate(spoofed, "BTC/USDT", 1000)

    spoofed = copy.deepcopy(packet)
    spoofed["spot"]["venues"][0]["timestamp_ms"] = -1
    with pytest.raises(ValueError):
        _validate(spoofed, "BTC/USDT", 1000)
