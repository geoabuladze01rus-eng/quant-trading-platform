"""T-Invest sandbox-only REST transport and bounded test-order client."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

import httpx

from quant_trading_platform.config import Settings, TradingMode
from quant_trading_platform.connectors.t_invest.client import TInvestSafetyError

SANDBOX_BASE_URL = "https://sandbox-invest-public-api.tbank.ru"
_SANDBOX_PREFIX = "/rest/tinkoff.public.invest.api.contract.v1.SandboxService/"
SANDBOX_METHODS = frozenset({
    "GetSandboxAccounts",
    "GetSandboxPortfolio",
    "GetSandboxPositions",
    "GetSandboxOrders",
    "GetSandboxOrderState",
    "PostSandboxOrder",
    "CancelSandboxOrder",
})


class SandboxTransport(Protocol):
    def call(self, method: str, parameters: dict[str, object]) -> dict[str, object]: ...


class TInvestSandboxTransport:
    """HTTP transport pinned to the official sandbox host and SandboxService methods."""

    def __init__(
        self,
        api_token: str,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_token.strip():
            raise TInvestSafetyError("T-Invest sandbox API token is not configured")
        self._client = httpx.Client(
            base_url=SANDBOX_BASE_URL,
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            transport=transport,
        )

    def call(self, method: str, parameters: dict[str, object]) -> dict[str, object]:
        if method not in SANDBOX_METHODS:
            raise TInvestSafetyError("T-Invest operation is outside the sandbox allowlist")
        try:
            response = self._client.post(f"{_SANDBOX_PREFIX}{method}", json=parameters)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            # Do not expose response bodies, headers, or auth material in errors.
            raise TInvestSafetyError("T-Invest sandbox request failed") from None
        if not isinstance(payload, dict):
            raise TInvestSafetyError("T-Invest sandbox returned an invalid response")
        return payload

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TInvestSandboxTransport:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class TInvestSandboxClient:
    """Bounded sandbox account access. It cannot address the production API host."""

    def __init__(self, settings: Settings, transport: SandboxTransport) -> None:
        self.settings = settings
        self.transport = transport

    def _assert_ready(self) -> None:
        if not self.settings.t_invest_api_token:
            raise TInvestSafetyError("T-Invest sandbox API token is not configured")
        if not self.settings.t_invest_sandbox:
            raise TInvestSafetyError("T-Invest sandbox mode is required")
        if self.settings.trading_mode != TradingMode.PAPER:
            raise TInvestSafetyError("T-Invest sandbox calls require paper mode")
        if self.settings.live_trading_enabled:
            raise TInvestSafetyError("Disable live trading before using T-Invest sandbox")

    def _account(self, account_id: str) -> str:
        value = account_id.strip()
        if not value:
            raise ValueError("sandbox account_id is required")
        configured = self.settings.t_invest_account_id
        if configured and configured != value:
            raise TInvestSafetyError("Account does not match configured T-Invest sandbox account")
        return value

    def _call(self, method: str, parameters: dict[str, object]) -> dict[str, object]:
        self._assert_ready()
        if method not in SANDBOX_METHODS:
            raise TInvestSafetyError("T-Invest operation is outside the sandbox allowlist")
        try:
            return self.transport.call(method, parameters)
        except TInvestSafetyError:
            raise
        except Exception:
            raise TInvestSafetyError("T-Invest sandbox request failed") from None

    def get_accounts(self) -> list[dict[str, object]]:
        response = self._call("GetSandboxAccounts", {})
        accounts = response.get("accounts")
        if not isinstance(accounts, list) or any(not isinstance(row, dict) for row in accounts):
            raise TInvestSafetyError("Invalid T-Invest sandbox accounts response")
        return [dict(row) for row in accounts]

    def get_portfolio(self, account_id: str) -> dict[str, object]:
        return self._call("GetSandboxPortfolio", {"accountId": self._account(account_id)})

    def get_positions(self, account_id: str) -> dict[str, object]:
        return self._call("GetSandboxPositions", {"accountId": self._account(account_id)})

    def get_orders(self, account_id: str) -> dict[str, object]:
        return self._call("GetSandboxOrders", {"accountId": self._account(account_id)})

    def place_limit_order(
        self,
        *,
        account_id: str,
        instrument_id: str,
        direction: str,
        quantity_lots: int,
        limit_price: Decimal,
        request_id: str | None = None,
    ) -> dict[str, object]:
        self._assert_ready()
        if not self.settings.t_invest_sandbox_orders_enabled:
            raise TInvestSafetyError("T-Invest sandbox order gate is disabled")
        account = self._account(account_id)
        instrument = instrument_id.strip()
        if not instrument:
            raise ValueError("instrument_id is required")
        if direction not in ("ORDER_DIRECTION_BUY", "ORDER_DIRECTION_SELL"):
            raise ValueError("direction must be a buy or sell sandbox order")
        if type(quantity_lots) is not int or not 1 <= quantity_lots <= self.settings.t_invest_sandbox_max_lots:
            raise ValueError("quantity_lots exceeds the configured sandbox limit")
        if not isinstance(limit_price, Decimal) or not limit_price.is_finite() or limit_price <= 0:
            raise ValueError("limit_price must be a positive finite Decimal")
        nano_price = limit_price * Decimal(1_000_000_000)
        if nano_price != nano_price.to_integral_value():
            raise ValueError("limit_price supports at most 9 decimal places")
        units, nano = divmod(int(nano_price), 1_000_000_000)
        order_id = str(UUID(request_id)) if request_id is not None else str(uuid4())
        return self._call(
            "PostSandboxOrder",
            {
                "accountId": account,
                "instrumentId": instrument,
                "direction": direction,
                "quantity": str(quantity_lots),
                "price": {"units": str(units), "nano": nano},
                "orderType": "ORDER_TYPE_LIMIT",
                "orderId": order_id,
            },
        )

    def cancel_order(self, *, account_id: str, order_id: str) -> dict[str, object]:
        self._assert_ready()
        if not self.settings.t_invest_sandbox_orders_enabled:
            raise TInvestSafetyError("T-Invest sandbox order gate is disabled")
        value = order_id.strip()
        if not value:
            raise ValueError("order_id is required")
        return self._call(
            "CancelSandboxOrder",
            {
                "accountId": self._account(account_id),
                "orderId": value,
                "orderIdType": "ORDER_ID_TYPE_REQUEST",
            },
        )
