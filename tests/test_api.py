from fastapi.testclient import TestClient

from quant_trading_platform.api.app import app

client = TestClient(app)


def test_dashboard_endpoints_are_available_and_safe() -> None:
    assert client.get("/health").json()["live_trading"] == "locked"
    assert client.get("/settings").json()["trading_mode"] == "paper"
    assert len(client.get("/venues").json()) == 4
    assert client.get("/opportunities").json()[0]["approved"] is True
    assert client.get("/risk").json()["live_trading_locked"] is True
    assert len(client.get("/audit").json()) >= 1
