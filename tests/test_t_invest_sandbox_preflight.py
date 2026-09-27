import json

import pytest

from quant_trading_platform.config import Settings, TradingMode
from scripts.t_invest_sandbox_preflight import evaluate, main


def config(**overrides: object) -> Settings:
    return Settings(_env_file=None, t_invest_api_token="fixture-token", **overrides)


def test_offline_preflight_never_calls_provider_or_prints_credential(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import t_invest_sandbox_preflight as preflight

    monkeypatch.setattr(preflight, "Settings", lambda: config())  # type: ignore[misc]
    assert main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "configuration_only"
    assert result["network_check"] == "not_requested"
    assert "fixture-token" not in json.dumps(result)
    assert "account_id" not in result


@pytest.mark.parametrize(
    "override",
    [
        {"t_invest_sandbox": False},
        {"trading_mode": TradingMode.LIVE},
        {"live_trading_enabled": True},
        {"live_order_acceptance_gate": True},
        {"t_invest_sandbox_orders_enabled": True},
    ],
)
def test_unsafe_configuration_never_runs_account_probe(
    override: dict[str, object],
) -> None:
    calls = 0

    def probe() -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        return [{"id": "sandbox-1"}]

    code, result = evaluate(config(**override), probe=probe)
    assert (code, result["status"]) == (2, "unsafe_configuration")
    assert calls == 0


def test_missing_token_blocks_network_probe() -> None:
    settings = Settings(_env_file=None, t_invest_api_token=None)
    code, result = evaluate(settings, probe=lambda: [{"id": "sandbox-1"}])
    assert code == 2
    assert result["status"] == "sandbox_token_missing"


def test_network_preflight_finds_account_without_exposing_id_or_token() -> None:
    code, result = evaluate(
        config(t_invest_account_id="sandbox-1"),
        probe=lambda: [{"id": "sandbox-1"}, {"id": "sandbox-2"}],
    )
    assert code == 0
    assert result["status"] == "sandbox_accounts_accessible"
    assert result["account_count"] == 2
    assert "sandbox-1" not in json.dumps(result)
    assert "fixture-token" not in json.dumps(result)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ([], "no_sandbox_accounts"),
        ([{"id": "another"}], "configured_account_not_found"),
        ([{"accountId": "sandbox-1"}], "sandbox_accounts_unavailable"),
    ],
)
def test_network_preflight_reports_actionable_safe_status(
    response: list[dict[str, object]], expected: str,
) -> None:
    code, result = evaluate(
        config(t_invest_account_id="sandbox-1"),
        probe=lambda: response,
    )
    assert code == 2
    assert result["status"] == expected
    assert "sandbox-1" not in json.dumps(result)


def test_network_error_is_redacted() -> None:
    def fail() -> list[dict[str, object]]:
        raise RuntimeError("Authorization: Bearer fixture-token")

    code, result = evaluate(config(), probe=fail)
    assert code == 2
    assert result["network_check"] == "failed"
    assert result["status"] == "sandbox_accounts_unavailable"
    assert "fixture-token" not in json.dumps(result)
