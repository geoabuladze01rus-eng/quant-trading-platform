import json
import sys

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

from quant_trading_platform.mcp.evidence_client import EvidenceClient
from quant_trading_platform.mcp.server import create_server


@pytest.mark.asyncio
async def test_only_one_read_only_tool_with_exact_symbol_enum():
    adapter = EvidenceClient()
    server = create_server(adapter)
    tools = await server.list_tools()
    assert [t.name for t in tools] == ["get_signal_evidence"]
    assert tools[0].inputSchema["properties"]["symbol"]["enum"] == [
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
    ]
    assert tools[0].annotations.readOnlyHint is True
    assert tools[0].annotations.destructiveHint is False
    assert tools[0].annotations.idempotentHint is True
    assert await server.list_resources() == []
    assert await server.list_prompts() == []
    await adapter.close()


@pytest.mark.asyncio
async def test_tool_calls_existing_backend_without_trading_or_polling(monkeypatch):
    from importlib import import_module

    api = import_module("quant_trading_platform.api.app")
    monkeypatch.setattr(api.app.state, "market_data", None, raising=False)
    monkeypatch.setattr(api.app.state, "derivatives", None, raising=False)
    monkeypatch.setattr(api.app.state, "liquidations", None, raising=False)
    monkeypatch.setattr(api.app.state, "liquidation_collectors", [], raising=False)
    monkeypatch.setattr(api, "now_ms", lambda: 1000)

    def forbidden(*a, **kw):
        pytest.fail("MCP called execution or upstream exchange polling")

    monkeypatch.setattr(
        "quant_trading_platform.connectors.crypto.client.PublicCryptoConnector.place_order",
        forbidden,
    )
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app)) as client:
        server = create_server(EvidenceClient(client=client, clock=lambda: 1001))
        response = await server.call_tool("get_signal_evidence", {"symbol": "BTC/USDT"})
        content, structured = response
        assert structured["symbol"] == "BTC/USDT"
        assert structured["quality"]["status"] == "insufficient"
        assert structured["execution"] == {"paper_only": True, "live_execution": False}
        assert json.loads(content[0].text) == structured
        with pytest.raises(ToolError):
            await server.call_tool("get_signal_evidence", {"symbol": "LTC/USDT"})
        with pytest.raises(ToolError):
            await server.call_tool("place_order", {"symbol": "BTC/USDT"})


@pytest.mark.asyncio
async def test_actual_stdio_handshake_discovery_and_sanitized_failure():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "quant_trading_platform.mcp.server"],
        env={"PYTHONPATH": "src", "SIGNAL_EVIDENCE_ORIGIN": "http://127.0.0.1:1"},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        result = await session.initialize()
        assert result.serverInfo.name == "Crypto Signal Data Hub"
        tools = await session.list_tools()
        assert [t.name for t in tools.tools] == ["get_signal_evidence"]
        unavailable = await session.call_tool("get_signal_evidence", {"symbol": "BTC/USDT"})
        assert unavailable.isError
        assert "signal_evidence_unavailable" in unavailable.content[0].text
        assert "127.0.0.1" not in unavailable.content[0].text
