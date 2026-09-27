"""Sanitized, read-only operator preflight for T-Invest API sandbox."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any

from quant_trading_platform.config import Settings, TradingMode
from quant_trading_platform.connectors.t_invest.sandbox import (
    TInvestSandboxClient,
    TInvestSandboxTransport,
)


def _probe_accounts(settings: Settings) -> list[dict[str, object]]:
    token = settings.t_invest_api_token
    if not token:
        raise ValueError("Sandbox token is missing")
    with TInvestSandboxTransport(token) as transport:
        return TInvestSandboxClient(settings, transport).get_accounts()


def evaluate(
    settings: Settings,
    *,
    probe: Callable[[], list[dict[str, object]]] | None = None,
) -> tuple[int, dict[str, Any]]:
    """No provider call unless explicitly probed and all safety gates are locked."""
    result: dict[str, Any] = {
        "environment": "sandbox",
        "mode": settings.trading_mode.value,
        "network_check": "not_requested" if probe is None else "blocked",
        "token_configured": bool(settings.t_invest_api_token),
        "account_configured": bool(settings.t_invest_account_id),
        "orders_locked": not settings.t_invest_sandbox_orders_enabled,
        "live_locked": not settings.live_trading_enabled,
    }
    if (
        not settings.t_invest_sandbox
        or settings.trading_mode != TradingMode.PAPER
        or settings.live_trading_enabled
        or settings.live_order_acceptance_gate
        or settings.t_invest_sandbox_orders_enabled
    ):
        result["status"] = "unsafe_configuration"
        return 2, result
    if not settings.t_invest_api_token or not settings.t_invest_api_token.strip():
        result["status"] = "sandbox_token_missing"
        return 2, result
    if probe is None:
        result["status"] = "configuration_only"
        return 0, result
    try:
        accounts = probe()
        if not all(
            isinstance(row, dict)
            and isinstance(identifier := row.get("id"), str)
            and bool(identifier.strip())
            for row in accounts
        ):
            raise ValueError("Invalid sandbox account response")
    except Exception:
        # Never print provider error bodies, account data, or credential material.
        result["network_check"] = "failed"
        result["status"] = "sandbox_accounts_unavailable"
        return 2, result

    result["network_check"] = "ok"
    result["account_count"] = len(accounts)
    if not accounts:
        result["status"] = "no_sandbox_accounts"
        return 2, result
    configured = settings.t_invest_account_id
    if configured and not any(account["id"] == configured for account in accounts):
        result["status"] = "configured_account_not_found"
        return 2, result
    result["status"] = "sandbox_accounts_accessible"
    return 0, result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T-Invest read-only sandbox preflight")
    parser.add_argument(
        "--check-accounts", action="store_true",
        help="Explicitly fetch sandbox account list; never places or cancels orders",
    )
    args = parser.parse_args(argv)
    try:
        settings = Settings()
    except Exception:
        print(json.dumps({"environment": "sandbox", "status": "invalid_configuration"}))
        return 2
    code, result = evaluate(
        settings,
        probe=(lambda: _probe_accounts(settings)) if args.check_accounts else None,
    )
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
