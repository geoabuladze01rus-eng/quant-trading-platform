"""Local, read-only API for the T-Invest sandbox account."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from quant_trading_platform.config import Settings
from quant_trading_platform.connectors.t_invest.client import TInvestSafetyError
from quant_trading_platform.connectors.t_invest.sandbox import TInvestSandboxClient

router = APIRouter(prefix="/t-invest/sandbox", tags=["t-invest-sandbox"])


def _trusted_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ):
        raise HTTPException(403, "Untrusted browser origin")


def _settings(request: Request) -> Settings:
    value = getattr(request.app.state, "t_invest_settings", None)
    return value if isinstance(value, Settings) else Settings(_env_file=None)


def _client(request: Request) -> TInvestSandboxClient:
    client = getattr(request.app.state, "t_invest_sandbox_client", None)
    if not isinstance(client, TInvestSandboxClient):
        raise HTTPException(503, "T-Invest sandbox is not configured or unavailable")
    return client


def _safe_payload(value: Any) -> Any:
    """Strip credential-like fields from provider payloads before returning JSON."""
    if isinstance(value, dict):
        return {
            str(key): _safe_payload(item)
            for key, item in value.items()
            if not any(
                secret in str(key).lower()
                for secret in ("token", "secret", "authorization")
            )
        }
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    return value


def _sandbox_call(action: Any) -> Any:
    try:
        return action()
    except TInvestSafetyError as error:
        raise HTTPException(409, str(error)) from None
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from None
    except Exception:
        raise HTTPException(502, "T-Invest sandbox request failed") from None


@router.get("/status")
def sandbox_status(request: Request) -> dict[str, object]:
    _trusted_origin(request)
    config = _settings(request)
    ready = (
        bool(config.t_invest_api_token)
        and config.t_invest_sandbox
        and config.trading_mode.value == "paper"
        and not config.live_trading_enabled
    )
    return {
        "provider": "t_invest",
        "environment": "sandbox",
        "configured": bool(config.t_invest_api_token),
        "ready": ready,
        "account_configured": bool(config.t_invest_account_id),
        "orders_enabled": False,
        "order_submission_available": False,
        "max_order_lots": config.t_invest_sandbox_max_lots,
        "live_trading": "locked",
        "live_execution": False,
    }


@router.get("/accounts")
def sandbox_accounts(request: Request) -> dict[str, object]:
    _trusted_origin(request)
    accounts = _sandbox_call(lambda: _client(request).get_accounts())
    config = _settings(request)
    configured_id = config.t_invest_account_id
    rows: list[dict[str, object]] = []
    for account in accounts:
        # GetSandboxAccounts returns GetAccountsResponse; Account uses "id".
        account_id = account.get("id")
        if not isinstance(account_id, str) or not account_id.strip():
            raise HTTPException(502, "Invalid T-Invest sandbox account response")
        if configured_id and account_id != configured_id:
            continue
        status = account.get("status")
        rows.append({
            "account_id": account_id,
            "status": status if isinstance(status, str) else "unknown",
        })
    return {"environment": "sandbox", "accounts": rows}


@router.get("/accounts/{account_id}/portfolio")
def sandbox_portfolio(account_id: str, request: Request) -> dict[str, object]:
    _trusted_origin(request)
    payload = _sandbox_call(lambda: _client(request).get_portfolio(account_id))
    return {"environment": "sandbox", "portfolio": _safe_payload(payload)}


@router.get("/accounts/{account_id}/positions")
def sandbox_positions(account_id: str, request: Request) -> dict[str, object]:
    _trusted_origin(request)
    payload = _sandbox_call(lambda: _client(request).get_positions(account_id))
    return {"environment": "sandbox", "positions": _safe_payload(payload)}


@router.get("/accounts/{account_id}/orders")
def sandbox_orders(account_id: str, request: Request) -> dict[str, object]:
    _trusted_origin(request)
    payload = _sandbox_call(lambda: _client(request).get_orders(account_id))
    return {"environment": "sandbox", "orders": _safe_payload(payload)}

@router.get("/accounts/{account_id}/order-price")
def sandbox_order_price(
    account_id: str,
    request: Request,
    instrument_id: str,
    direction: str,
    quantity_lots: int,
    limit_price: Decimal,
) -> dict[str, object]:
    """Read-only provider estimate; never submits, approves, or reserves an order."""
    _trusted_origin(request)
    estimate = _sandbox_call(
        lambda: _client(request).get_order_price(
            account_id=account_id,
            instrument_id=instrument_id,
            direction=direction,
            quantity_lots=quantity_lots,
            limit_price=limit_price,
        )
    )
    return {
        "environment": "sandbox",
        "status": "estimate_only",
        "approved": False,
        "order_submission_available": False,
        "reason": "provider_estimate_is_not_risk_approval",
        "estimate": _safe_payload(estimate),
    }
