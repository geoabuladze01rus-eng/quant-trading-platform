import pytest

from quant_trading_platform.config import Settings
from quant_trading_platform.connectors.t_invest.client import TInvestClient, TInvestSafetyError


def test_t_invest_read_requires_token() -> None:
    client = TInvestClient(Settings(t_invest_api_token=None))

    with pytest.raises(TInvestSafetyError, match="token is not configured"):
        client.assert_read_ready()


def test_t_invest_live_orders_blocked_in_sandbox() -> None:
    client = TInvestClient(
        Settings(
            t_invest_api_token="test-token",
            t_invest_sandbox=True,
            live_trading_enabled=True,
        )
    )

    with pytest.raises(TInvestSafetyError, match="blocked in sandbox"):
        client.assert_live_order_allowed()


def test_t_invest_live_orders_require_global_gate() -> None:
    client = TInvestClient(
        Settings(
            t_invest_api_token="test-token",
            t_invest_sandbox=False,
            live_trading_enabled=False,
        )
    )

    with pytest.raises(TInvestSafetyError, match="Live trading is disabled"):
        client.assert_live_order_allowed()
