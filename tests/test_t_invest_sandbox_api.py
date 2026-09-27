from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from quant_trading_platform.api import app as api
from quant_trading_platform.config import Settings
from quant_trading_platform.connectors.t_invest.sandbox import TInvestSandboxClient


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, parameters: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, parameters))
        if method == "GetSandboxAccounts":
            return {"accounts": [
                {"accountId": "sandbox-1", "status": "ACCOUNT_STATUS_OPEN", "token": "hidden"},
                {"accountId": "sandbox-2", "status": "ACCOUNT_STATUS_OPEN"},
            ]}
        if method == "GetSandboxPortfolio":
            return {"accountId": "sandbox-1", "positions": [], "accessToken": "hidden"}
        if method == "GetSandboxPositions":
            return {"accountId": "sandbox-1", "positions": []}
        if method == "GetSandboxOrders":
            return {"orders": []}
        raise AssertionError(f"Unexpected method: {method}")


class FailingTransport:
    def call(self, method: str, parameters: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("Authorization: Bearer do-not-leak")


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    config = Settings(
        _env_file=None,
        t_invest_api_token="sandbox-test-token",
        t_invest_sandbox=True,
    )
    transport = FakeTransport()
    sandbox_client = TInvestSandboxClient(config, transport)
    monkeypatch.setattr(api.state, "t_invest_settings", config, raising=False)
    monkeypatch.setattr(api.state, "t_invest_sandbox_client", sandbox_client, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api),
        base_url="http://test",
    ) as session:
        yield session


@pytest.mark.asyncio
async def test_status_is_safe_and_does_not_expose_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Settings(_env_file=None, t_invest_api_token="sandbox-secret")
    monkeypatch.setattr(api.state, "t_invest_settings", config, raising=False)
    monkeypatch.setattr(api.state, "t_invest_sandbox_client", None, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api),
        base_url="http://test",
    ) as session:
        response = await session.get("/t-invest/sandbox/status")
    assert response.status_code == 200
    assert response.json() == {
        "provider": "t_invest",
        "environment": "sandbox",
        "configured": True,
        "ready": True,
        "account_configured": False,
        "orders_enabled": False,
        "order_submission_available": False,
        "max_order_lots": 1,
        "live_trading": "locked",
        "live_execution": False,
    }
    assert "sandbox-secret" not in response.text


@pytest.mark.asyncio
async def test_accounts_are_filtered_to_configured_sandbox_account(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Settings(
        _env_file=None,
        t_invest_api_token="sandbox-test-token",
        t_invest_account_id="sandbox-1",
    )
    monkeypatch.setattr(api.state, "t_invest_settings", config, raising=False)
    response = await client.get("/t-invest/sandbox/accounts")
    assert response.status_code == 200
    assert response.json() == {
        "environment": "sandbox",
        "accounts": [{"account_id": "sandbox-1", "status": "ACCOUNT_STATUS_OPEN"}],
    }
    assert "hidden" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "method", "expected_key"),
    [
        ("/t-invest/sandbox/accounts/sandbox-1/portfolio", "GetSandboxPortfolio", "portfolio"),
        ("/t-invest/sandbox/accounts/sandbox-1/positions", "GetSandboxPositions", "positions"),
        ("/t-invest/sandbox/accounts/sandbox-1/orders", "GetSandboxOrders", "orders"),
    ],
)
async def test_sandbox_reads_use_only_the_sandbox_client(
    client: httpx.AsyncClient,
    path: str,
    method: str,
    expected_key: str,
) -> None:
    response = await client.get(path)
    assert response.status_code == 200
    assert response.json()["environment"] == "sandbox"
    assert expected_key in response.json()
    assert "hidden" not in response.text


@pytest.mark.asyncio
async def test_sandbox_routes_reject_untrusted_browser_origins(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/t-invest/sandbox/accounts",
        headers={"Origin": "https://unexpected.example"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_sandbox_route_does_not_leak_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Settings(_env_file=None, t_invest_api_token="sandbox-test-token")
    sandbox_client = TInvestSandboxClient(config, FailingTransport())
    monkeypatch.setattr(api.state, "t_invest_settings", config, raising=False)
    monkeypatch.setattr(api.state, "t_invest_sandbox_client", sandbox_client, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api),
        base_url="http://test",
    ) as session:
        response = await session.get("/t-invest/sandbox/accounts")
    assert response.status_code == 409
    assert "do-not-leak" not in response.text



def test_t_invest_sandbox_api_exposes_only_read_routes() -> None:
    paths = {
        path: methods
        for path, methods in api.openapi()["paths"].items()
        if path.startswith("/t-invest/sandbox/")
    }
    assert paths
    assert all(set(methods) == {"get"} for methods in paths.values())
