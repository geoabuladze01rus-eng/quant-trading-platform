from dataclasses import dataclass

from quant_trading_platform.config import Settings


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
        if self.is_sandbox:
            raise TInvestSafetyError("T-Invest live orders are blocked in sandbox mode")
        if not self.settings.live_trading_enabled:
            raise TInvestSafetyError("Live trading is disabled by global safety gate")

    def get_accounts(self) -> list[dict[str, str]]:
        self.assert_read_ready()
        raise NotImplementedError("T-Invest account loading is not implemented yet")

    def get_portfolio(self, account_id: str) -> dict[str, object]:
        self.assert_read_ready()
        raise NotImplementedError("T-Invest portfolio loading is not implemented yet")

    def place_order(self) -> None:
        self.assert_live_order_allowed()
        raise NotImplementedError("T-Invest live order placement is not implemented yet")
