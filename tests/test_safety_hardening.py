from decimal import Decimal

import pytest
from pydantic import ValidationError

from quant_trading_platform.config import Settings, TradingMode
from quant_trading_platform.connectors.crypto.client import (
    BinanceConnector,
    BybitConnector,
    OKXConnector,
)
from quant_trading_platform.connectors.t_invest.client import TInvestClient
from quant_trading_platform.safety import SafetyError, assert_safe_startup


@pytest.mark.parametrize("connector", [BinanceConnector, BybitConnector, OKXConnector])
@pytest.mark.parametrize("all_gates", [False, True])
def test_no_crypto_order_implementation_even_with_all_gates(
    connector: type[BinanceConnector | BybitConnector | OKXConnector], all_gates: bool,
) -> None:
    settings = Settings(
        _env_file=None, trading_mode=TradingMode.LIVE if all_gates else TradingMode.PAPER,
        live_trading_enabled=all_gates, live_order_acceptance_gate=all_gates,
    )
    with pytest.raises((SafetyError, NotImplementedError)):
        connector(settings).place_order("BTC/USDT", "buy", Decimal("0.1"))


def test_t_invest_has_no_order_implementation_after_gates() -> None:
    settings = Settings(
        _env_file=None, trading_mode=TradingMode.LIVE, live_trading_enabled=True,
        live_order_acceptance_gate=True, t_invest_sandbox=False,
    )
    with pytest.raises(NotImplementedError):
        TInvestClient(settings).place_order()


def test_startup_rejects_live_even_after_all_gates() -> None:
    settings = Settings(
        _env_file=None, trading_mode=TradingMode.LIVE, live_trading_enabled=True,
        live_order_acceptance_gate=True,
    )
    with pytest.raises(SafetyError, match="not implemented"):
        assert_safe_startup(settings)


@pytest.mark.parametrize("limit", [2.1, float("nan"), float("inf"), -1.0])
def test_unsafe_daily_loss_settings_rejected(limit: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_daily_loss_pct=limit)


def test_secrets_not_in_settings_repr_or_serialization() -> None:
    settings = Settings(
        _env_file=None, binance_api_key="synthetic-secret", binance_api_secret="synthetic-secret",
        bybit_api_key="synthetic-secret", bybit_api_secret="synthetic-secret",
        okx_api_key="synthetic-secret", okx_api_secret="synthetic-secret",
        okx_api_passphrase="synthetic-secret", t_invest_api_token="synthetic-secret",
        telegram_bot_token="synthetic-secret",
    )
    assert "synthetic-secret" not in repr(settings)
    assert "synthetic-secret" not in settings.model_dump_json()
