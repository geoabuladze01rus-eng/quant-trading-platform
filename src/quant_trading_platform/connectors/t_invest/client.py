from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from quant_trading_platform.config import Settings
from quant_trading_platform.models import MarketType
from quant_trading_platform.safety import SafetyError, assert_live_order_allowed


class TInvestSafetyError(RuntimeError):
    """Raised when a T-Invest operation violates project safety gates."""


class SandboxReadTransport(Protocol):
    """Injectable sandbox transport; no credentials or network implementation here."""

    def read(self, method: str, parameters: dict[str, object]) -> dict[str, object]: ...


READ_METHODS = frozenset({
    "SandboxService/GetSandboxAccounts", "SandboxService/GetSandboxPortfolio",
    "InstrumentsService/Shares", "MarketDataService/GetCandles", "MarketDataService/GetOrderBook",
})


@dataclass(frozen=True)
class TInvestClient:
    settings: Settings
    transport: SandboxReadTransport | None = field(default=None, repr=False)
    market_type: MarketType = field(default=MarketType.RUSSIAN_STOCKS, init=False)

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.t_invest_api_token)

    @property
    def is_sandbox(self) -> bool:
        return self.settings.t_invest_sandbox

    def assert_read_ready(self) -> None:
        if not self.is_configured:
            raise TInvestSafetyError("T-Invest API token is not configured")
        if not self.is_sandbox:
            raise TInvestSafetyError("T-Invest reads require sandbox mode")

    def _read(self, method: str, parameters: dict[str, object]) -> dict[str, object]:
        self.assert_read_ready()
        if method not in READ_METHODS:
            raise TInvestSafetyError("T-Invest operation is not read-only")
        if self.transport is None:
            raise TInvestSafetyError("T-Invest sandbox read transport is not configured")
        try:
            return self.transport.read(method, parameters)
        except Exception:
            # Transport exceptions can contain authorization headers. Never surface them.
            raise TInvestSafetyError("T-Invest sandbox read failed") from None

    @staticmethod
    def _items(response: dict[str, object], key: str) -> list[dict[str, object]]:
        items = response.get(key)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise TInvestSafetyError("Invalid T-Invest read response")
        return [dict(item) for item in items]

    def assert_live_order_allowed(self) -> None:
        try:
            assert_live_order_allowed(self.settings, t_invest_sandbox=self.is_sandbox)
        except SafetyError as error:
            raise TInvestSafetyError(str(error)) from error

    def get_accounts(self) -> list[dict[str, object]]:
        return self._items(self._read("SandboxService/GetSandboxAccounts", {}), "accounts")

    def get_portfolio(self, account_id: str) -> dict[str, object]:
        if not account_id.strip():
            raise ValueError("account_id is required")
        return self._read("SandboxService/GetSandboxPortfolio", {"accountId": account_id})

    def get_instruments(self) -> list[dict[str, object]]:
        response = self._read("InstrumentsService/Shares", {
            "instrumentStatus": "INSTRUMENT_STATUS_BASE",
        })
        return [item for item in self._items(response, "instruments")
                if item.get("currency") == "rub" and item.get("classCode") == "TQBR"]

    def get_candles(self, instrument_id: str, interval: str = "1h") -> list[dict[str, object]]:
        if not instrument_id.strip() or interval not in ("1m", "5m", "1h", "1d"):
            raise ValueError("Invalid candle instrument or interval")
        intervals = {"1m": "1_MIN", "5m": "5_MIN", "1h": "HOUR", "1d": "DAY"}
        now = datetime.now(UTC)
        response = self._read("MarketDataService/GetCandles", {
            "instrumentId": instrument_id, "interval": f"CANDLE_INTERVAL_{intervals[interval]}",
            "from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat(),
        })
        return self._items(response, "candles")

    def get_order_book(self, instrument_id: str, depth: int = 20) -> dict[str, object]:
        if not instrument_id.strip() or depth not in (1, 10, 20, 30, 50):
            raise ValueError("Invalid order book instrument or depth")
        return self._read("MarketDataService/GetOrderBook", {
            "instrumentId": instrument_id, "depth": depth,
        })

    def place_order(self) -> None:
        self.assert_live_order_allowed()
        raise NotImplementedError("T-Invest live order placement is not implemented yet")
