from quant_trading_platform.config import Settings, TradingMode


class SafetyError(RuntimeError):
    """Raised when startup would violate a trading safety gate."""


def assert_safe_startup(settings: Settings) -> None:
    if settings.trading_mode == TradingMode.LIVE and not settings.live_trading_enabled:
        raise SafetyError("LIVE mode requires LIVE_TRADING_ENABLED=true")

    if settings.trading_mode == TradingMode.LIVE and settings.max_daily_loss_pct > 2:
        raise SafetyError("Live trading max daily loss must not exceed 2%")
    if settings.trading_mode == TradingMode.LIVE:
        assert_live_order_allowed(settings)
        raise SafetyError("Live execution is not implemented; use paper/read-only mode")


def assert_live_order_allowed(settings: Settings, *, t_invest_sandbox: bool = False) -> None:
    """Central hard gate for every future live order implementation."""
    if not settings.live_trading_enabled:
        raise SafetyError("Live trading is disabled by global safety gate")
    if t_invest_sandbox:
        raise SafetyError("T-Invest live orders are blocked in sandbox mode")
    if settings.trading_mode != TradingMode.LIVE:
        raise SafetyError("Live order requires TRADING_MODE=live")
    if not settings.live_order_acceptance_gate:
        raise SafetyError("Live order requires explicit acceptance gate")
