from quant_trading_platform.config import Settings, TradingMode


class SafetyError(RuntimeError):
    """Raised when startup would violate a trading safety gate."""


def assert_safe_startup(settings: Settings) -> None:
    if settings.trading_mode == TradingMode.LIVE and not settings.live_trading_enabled:
        raise SafetyError("LIVE mode requires LIVE_TRADING_ENABLED=true")

    if settings.trading_mode == TradingMode.LIVE and settings.max_daily_loss_pct > 2:
        raise SafetyError("Live trading max daily loss must not exceed 2%")
