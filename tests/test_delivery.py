"""Guard the container entrypoint and its safe, keyless development defaults."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_starts_fastapi_on_exposed_port() -> None:
    lines = (ROOT / "Dockerfile").read_text().splitlines()
    commands = [json.loads(line.removeprefix("CMD ")) for line in lines if line.startswith("CMD ")]
    assert commands == [[
        "uvicorn", "quant_trading_platform.api.app:app", "--host", "0.0.0.0", "--port", "8000",
    ]]
    assert "EXPOSE 8000" in lines


def test_compose_preserves_entrypoint_and_locked_defaults_without_env_file() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    assert '"8000:8000"' in compose
    assert "command:" not in compose
    assert "env_file:" not in compose
    assert "TRADING_MODE: paper" in compose
    assert 'LIVE_TRADING_ENABLED: "false"' in compose
    assert 'LIVE_ORDER_ACCEPTANCE_GATE: "false"' in compose
    assert 'T_INVEST_SANDBOX: "true"' in compose


def test_docker_build_context_excludes_local_secrets() -> None:
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert {".env", ".env.*", "**/.env", "**/.env.*", ".git"} <= set(ignored)
