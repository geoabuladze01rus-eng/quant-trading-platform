import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from quant_trading_platform.config import Settings, TradingMode
from quant_trading_platform.connectors.t_invest.client import TInvestSafetyError
from quant_trading_platform.connectors.t_invest.sandbox import (
    SANDBOX_BASE_URL,
    TInvestSandboxClient,
    TInvestSandboxTransport,
)


class FixtureTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, parameters: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, parameters))
        return {"accounts": [{"id": "sandbox-1"}]}


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, t_invest_api_token="fixture-token", **overrides)


def test_transport_is_pinned_to_sandbox_and_posts_only_allowlisted_service_methods() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"accounts": [{"id": "sandbox-1"}]})

    transport = TInvestSandboxTransport(
        "fixture-token", transport=httpx.MockTransport(handler)
    )
    client = TInvestSandboxClient(settings(), transport)

    assert client.get_accounts() == [{"id": "sandbox-1"}]
    assert seen[0].url.host == "sandbox-invest-public-api.tbank.ru"
    assert str(seen[0].url).startswith(SANDBOX_BASE_URL)
    assert seen[0].url.path.endswith(
        "/rest/tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxAccounts"
    )
    assert seen[0].headers["Authorization"] == "Bearer fixture-token"
    transport.close()


def test_transport_rejects_production_order_method_before_network_call() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    transport = TInvestSandboxTransport(
        "fixture-token", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(TInvestSafetyError, match="sandbox allowlist"):
        transport.call("OrdersService/PostOrder", {})
    assert calls == 0
    transport.close()


def test_sandbox_limit_order_is_gated_and_uses_decimal_price_and_idempotency_id() -> None:
    transport = FixtureTransport()
    client = TInvestSandboxClient(
        settings(t_invest_sandbox_orders_enabled=True), transport
    )
    request_id = str(uuid4())

    client.place_limit_order(
        account_id="sandbox-1",
        instrument_id="SBER_TQBR",
        direction="ORDER_DIRECTION_BUY",
        quantity_lots=1,
        limit_price=Decimal("123.45"),
        request_id=request_id,
    )

    assert transport.calls == [(
        "PostSandboxOrder",
        {
            "accountId": "sandbox-1",
            "instrumentId": "SBER_TQBR",
            "direction": "ORDER_DIRECTION_BUY",
            "quantity": "1",
            "price": {"units": "123", "nano": 450_000_000},
            "orderType": "ORDER_TYPE_LIMIT",
            "orderId": request_id,
        },
    )]


def test_sandbox_order_gate_defaults_off() -> None:
    transport = FixtureTransport()
    client = TInvestSandboxClient(settings(), transport)

    with pytest.raises(TInvestSafetyError, match="gate is disabled"):
        client.place_limit_order(
            account_id="sandbox-1",
            instrument_id="SBER_TQBR",
            direction="ORDER_DIRECTION_BUY",
            quantity_lots=1,
            limit_price=Decimal("100"),
        )

    assert transport.calls == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"t_invest_sandbox": False}, "sandbox mode"),
        ({"trading_mode": TradingMode.LIVE}, "paper mode"),
        ({"live_trading_enabled": True}, "Disable live trading"),
    ],
)
def test_sandbox_calls_fail_closed_for_unsafe_settings(
    overrides: dict[str, object], reason: str
) -> None:
    transport = FixtureTransport()
    client = TInvestSandboxClient(settings(**overrides), transport)

    with pytest.raises(TInvestSafetyError, match=reason):
        client.get_accounts()

    assert transport.calls == []


def test_sandbox_order_enforces_lot_limit_account_and_limit_order_inputs() -> None:
    transport = FixtureTransport()
    client = TInvestSandboxClient(
        settings(
            t_invest_sandbox_orders_enabled=True,
            t_invest_sandbox_max_lots=1,
            t_invest_account_id="sandbox-1",
        ),
        transport,
    )

    with pytest.raises(ValueError, match="configured sandbox limit"):
        client.place_limit_order(
            account_id="sandbox-1",
            instrument_id="SBER_TQBR",
            direction="ORDER_DIRECTION_BUY",
            quantity_lots=2,
            limit_price=Decimal("100"),
        )
    with pytest.raises(TInvestSafetyError, match="does not match"):
        client.get_portfolio("another-account")
    with pytest.raises(ValueError, match="9 decimal places"):
        client.place_limit_order(
            account_id="sandbox-1",
            instrument_id="SBER_TQBR",
            direction="ORDER_DIRECTION_BUY",
            quantity_lots=1,
            limit_price=Decimal("1.0000000001"),
        )
    with pytest.raises(ValueError, match="buy or sell"):
        client.place_limit_order(
            account_id="sandbox-1",
            instrument_id="SBER_TQBR",
            direction="ORDER_DIRECTION_UNSPECIFIED",
            quantity_lots=1,
            limit_price=Decimal("100"),
        )

    assert transport.calls == []


def test_http_errors_are_sanitized_and_do_not_echo_api_token() -> None:
    secret = "fixture-token"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"rejected {secret}")

    transport = TInvestSandboxTransport(secret, transport=httpx.MockTransport(handler))
    with pytest.raises(TInvestSafetyError) as error:
        transport.call("GetSandboxAccounts", {})
    assert secret not in str(error.value)
    transport.close()


def test_sandbox_price_preview_never_posts_order_even_with_order_gate_off() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"totalOrderAmount": {"units": "123"}})

    transport = TInvestSandboxTransport(
        "fixture-token", transport=httpx.MockTransport(handler)
    )
    client = TInvestSandboxClient(settings(), transport)
    estimate = client.get_order_price(
        account_id="sandbox-1",
        instrument_id="SBER_TQBR",
        direction="ORDER_DIRECTION_BUY",
        quantity_lots=1,
        limit_price=Decimal("123.45"),
    )
    assert estimate["totalOrderAmount"] == {"units": "123"}
    assert len(seen) == 1
    assert seen[0].url.host == "sandbox-invest-public-api.tbank.ru"
    assert seen[0].url.path.endswith(".SandboxService/GetSandboxOrderPrice")
    assert seen[0].method == "POST"
    assert json.loads(seen[0].content) == {
        "accountId": "sandbox-1",
        "instrumentId": "SBER_TQBR",
        "direction": "ORDER_DIRECTION_BUY",
        "quantity": "1",
        "price": {"units": "123", "nano": 450_000_000},
    }
    transport.close()


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"account_id": "other"}, TInvestSafetyError),
        ({"instrument_id": " "}, ValueError),
        ({"direction": "ORDER_DIRECTION_UNSPECIFIED"}, ValueError),
        ({"quantity_lots": 2}, ValueError),
        ({"limit_price": Decimal("NaN")}, ValueError),
        ({"limit_price": Decimal("0")}, ValueError),
        ({"limit_price": Decimal("0.0000000001")}, ValueError),
    ],
)
def test_price_preview_rejects_invalid_inputs_before_transport(
    changes: dict[str, object], error: type[Exception]
) -> None:
    transport = FixtureTransport()
    client = TInvestSandboxClient(
        settings(t_invest_account_id="sandbox-1", t_invest_sandbox_max_lots=1),
        transport,
    )
    values: dict[str, object] = {
        "account_id": "sandbox-1",
        "instrument_id": "SBER_TQBR",
        "direction": "ORDER_DIRECTION_BUY",
        "quantity_lots": 1,
        "limit_price": Decimal("100"),
    }
    values.update(changes)
    with pytest.raises(error):
        client.get_order_price(**values)  # type: ignore[arg-type]
    assert transport.calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"t_invest_sandbox": False},
        {"trading_mode": TradingMode.LIVE},
        {"live_trading_enabled": True},
    ],
)
def test_price_preview_fails_closed_in_unsafe_modes(
    overrides: dict[str, object],
) -> None:
    transport = FixtureTransport()
    client = TInvestSandboxClient(settings(**overrides), transport)
    with pytest.raises(TInvestSafetyError):
        client.get_order_price(
            account_id="sandbox-1",
            instrument_id="SBER_TQBR",
            direction="ORDER_DIRECTION_BUY",
            quantity_lots=1,
            limit_price=Decimal("100"),
        )
    assert transport.calls == []
