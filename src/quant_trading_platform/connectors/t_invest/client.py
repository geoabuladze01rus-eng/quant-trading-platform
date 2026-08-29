from dataclasses import dataclass

from quant_trading_platform.config import Settings
from quant_trading_platform.safety import SafetyError, assert_live_order_allowed


class TInvestSafetyError(RuntimeError):
    """Raised when a T-Invest operation violates project safety gates."""


@dataclass(frozen=True)
class TInvestClient:
    settings: Settings

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.t_invest_api_token)

    @property
    def is_sandbox(self) -> bool:
        return self.settings.t_invest_sandbox

    def assert_read_ready(self) -> None:
        if not self.is_configured:
            raise TInvestSafetyError("T-Invest API token is not configured")

    def assert_live_order_allowed(self) -> None:
        try:
            assert_live_order_allowed(self.settings, t_invest_sandbox=self.is_sandbox)
        except SafetyError as error:
            raise TInvestSafetyError(str(error)) from error

    def get_accounts(self) -> list[dict[str, str]]:
        self.assert_read_ready()
        return [{"id": self.settings.t_invest_account_id or "sandbox-account", "status": "sandbox"}]

    def get_portfolio(self, account_id: str) -> dict[str, object]:
        self.assert_read_ready()
        return {"account_id": account_id, "currency": "RUB", "total_amount": "0", "positions": []}

    def get_instruments(self) -> list[dict[str, str]]:
        self.assert_read_ready()
        return [{"figi": "MOCK_SBER", "ticker": "SBER", "currency": "RUB"}]

    def get_candles(self, instrument_id: str, interval: str = "1h") -> list[dict[str, str]]:
        self.assert_read_ready()
        return [{"instrument_id": instrument_id, "interval": interval, "close": "0"}]

    def place_order(self) -> None:
        self.assert_live_order_allowed()
        raise NotImplementedError("T-Invest live order placement is not implemented yet")
