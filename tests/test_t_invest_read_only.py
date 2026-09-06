import pytest

from quant_trading_platform.config import Settings, TradingMode
from quant_trading_platform.connectors.t_invest.client import TInvestClient, TInvestSafetyError
from quant_trading_platform.models import MarketType


class FixtureTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def read(self, method: str, parameters: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, parameters))
        return {
            "accounts": [{"id": "sandbox-fixture"}], "positions": [],
            "instruments": [
                {"ticker": "SBER", "currency": "rub", "classCode": "TQBR"},
                {"ticker": "FOREIGN", "currency": "usd", "classCode": "SPBXM"},
            ], "candles": [], "bids": [], "asks": [],
        }


def test_sandbox_reads_use_only_read_methods_and_keep_russian_market() -> None:
    transport = FixtureTransport()
    client = TInvestClient(Settings(_env_file=None, t_invest_api_token="fixture"), transport)
    assert client.market_type == MarketType.RUSSIAN_STOCKS
    assert client.get_accounts() == [{"id": "sandbox-fixture"}]
    assert client.get_portfolio("sandbox-fixture")["positions"] == []
    assert [item["ticker"] for item in client.get_instruments()] == ["SBER"]
    assert client.get_candles("SBER_TQBR") == []
    client.get_order_book("SBER_TQBR")
    assert [call[0] for call in transport.calls] == [
        "SandboxService/GetSandboxAccounts", "SandboxService/GetSandboxPortfolio",
        "InstrumentsService/Shares", "MarketDataService/GetCandles",
        "MarketDataService/GetOrderBook",
    ]


@pytest.mark.parametrize("sandbox", [False, True])
def test_order_never_reaches_transport_even_with_all_gates(sandbox: bool) -> None:
    transport = FixtureTransport()
    client = TInvestClient(Settings(
        _env_file=None, t_invest_api_token="fixture", t_invest_sandbox=sandbox,
        trading_mode=TradingMode.LIVE, live_trading_enabled=True, live_order_acceptance_gate=True,
    ), transport)
    with pytest.raises((TInvestSafetyError, NotImplementedError)):
        client.place_order()
    assert transport.calls == []


def test_production_read_is_blocked_before_transport() -> None:
    transport = FixtureTransport()
    client = TInvestClient(Settings(
        _env_file=None, t_invest_api_token="fixture", t_invest_sandbox=False,
    ), transport)
    with pytest.raises(TInvestSafetyError, match="sandbox"):
        client.get_accounts()
    assert transport.calls == []


def test_unconfigured_transport_cannot_return_fake_portfolio() -> None:
    client = TInvestClient(Settings(_env_file=None, t_invest_api_token="fixture"))
    with pytest.raises(TInvestSafetyError, match="transport is not configured"):
        client.get_portfolio("sandbox-fixture")


def test_mutating_rpc_is_rejected_before_transport() -> None:
    transport = FixtureTransport()
    client = TInvestClient(Settings(_env_file=None, t_invest_api_token="fixture"), transport)
    with pytest.raises(TInvestSafetyError, match="not read-only"):
        client._read("SandboxService/PostSandboxOrder", {})
    assert transport.calls == []
