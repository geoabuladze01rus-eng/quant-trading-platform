from decimal import Decimal

import pytest

from quant_trading_platform.config import Settings, TradingMode
from quant_trading_platform.connectors.crypto.client import BinanceConnector
from quant_trading_platform.safety import SafetyError, assert_live_order_allowed


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (Settings(), "disabled"),
        (Settings(live_trading_enabled=True), "TRADING_MODE=live"),
        (Settings(trading_mode=TradingMode.LIVE, live_trading_enabled=True), "acceptance gate"),
    ],
)
def test_live_orders_require_every_safety_gate(settings: Settings, message: str) -> None:
    with pytest.raises(SafetyError, match=message):
        assert_live_order_allowed(settings)


def test_crypto_place_order_is_blocked_by_default() -> None:
    with pytest.raises(SafetyError, match="disabled"):
        BinanceConnector(Settings()).place_order("BTC/USDT", "buy", Decimal("0.01"))
