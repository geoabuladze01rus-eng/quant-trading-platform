from quant_trading_platform.config import MarketScope, Settings, TradingMode


def test_default_settings_are_safe() -> None:
    settings = Settings()

    assert settings.trading_mode == TradingMode.PAPER
    assert settings.market_scope == MarketScope.MIXED
    assert settings.live_trading_enabled is False
    assert settings.t_invest_sandbox is True
    assert settings.t_invest_sandbox_orders_enabled is False
    assert settings.t_invest_sandbox_max_lots == 1
    assert settings.telegram_chat_id is None
