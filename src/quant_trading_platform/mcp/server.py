"""Single read-only MCP tool over stdio; no web listener or trading API imports."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from os import environ
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from quant_trading_platform.mcp.evidence_client import EvidenceClient

SignalSymbol = Literal["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def create_server(adapter: EvidenceClient) -> FastMCP[None]:
    @asynccontextmanager
    async def lifespan(server: FastMCP[None]) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await adapter.close()

    server = FastMCP[None](
        "Crypto Signal Data Hub",
        instructions=(
            "Read-only market evidence. Insufficient or unavailable evidence cannot count as "
            "confirmation. This tool gives no trading directive and cannot execute orders."
        ),
        lifespan=lifespan,
        log_level="ERROR",
    )

    @server.tool(
        name="get_signal_evidence",
        description="Read cached spot, derivatives and partial liquidations for a USDT pair.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
        structured_output=True,
    )
    async def get_signal_evidence(symbol: SignalSymbol) -> dict[str, object]:
        return await adapter.get_signal_evidence(symbol)

    return server


def main() -> None:
    origin = environ.get("SIGNAL_EVIDENCE_ORIGIN", "http://127.0.0.1:8000")
    try:
        adapter = EvidenceClient(origin)
    except ValueError:
        raise SystemExit("Invalid signal evidence origin") from None
    create_server(adapter).run(transport="stdio")


if __name__ == "__main__":
    main()
