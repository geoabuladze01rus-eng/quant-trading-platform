import pytest

from quant_trading_platform.config import MarketScope, Settings, TradingMode


def test_default_settings_are_safe() -> None:
    settings = Settings()

    assert settings.trading_mode == TradingMode.PAPER
    assert settings.market_scope == MarketScope.MIXED
    assert settings.live_trading_enabled is False
    assert settings.t_invest_sandbox is True
    assert settings.market_data_symbols == ("BTC/USDT", "ETH/USDT", "LTC/USDT")
    assert settings.paper_initial_ltc == 10


def test_market_data_symbols_reject_unsupported_or_ambiguous_pairs() -> None:
    for symbols in (("BTC/USDT", "BTC/USDT"), ("BTC/USD",), ()):
        try:
            Settings(_env_file=None, market_data_symbols=symbols)
        except ValueError:
            continue
        raise AssertionError(f"unsafe symbol universe accepted: {symbols}")


def test_negative_initial_ltc_balance_is_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, paper_initial_ltc="-0.00000001")
