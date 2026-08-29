from quant_trading_platform.config import Settings
from quant_trading_platform.safety import assert_safe_startup


def main() -> None:
    settings = Settings()
    assert_safe_startup(settings)
    print(
        "Quant Trading Platform started "
        f"in {settings.trading_mode.value} mode. Live trading: {settings.live_trading_enabled}"
    )


if __name__ == "__main__":
    main()
