"""One hosted process: public GET dashboard, private evidence MCP, existing collectors."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hmac import compare_digest
from os import environ
from pathlib import Path

import httpx
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from quant_trading_platform.mcp.evidence_client import EvidenceClient
from quant_trading_platform.mcp.server import create_server

READ_PATHS = frozenset({
    '/health', '/readiness', '/settings', '/venues', '/opportunities', '/risk', '/audit',
    '/reconciliation', '/paper/account', '/paper/balances', '/paper/orders',
    '/paper/fills', '/paper/positions', '/paper/performance', '/paper/reconciliation',
    '/paper/residual-exposure', '/paper/okx/robot', '/paper/okx/account',
    '/paper/okx/history', '/strategies/spot-signals',
    '/signal-evidence/BTC/USDT', '/signal-evidence/ETH/USDT', '/signal-evidence/SOL/USDT',
})


class ReadOnlyGateway:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope['path'][len(scope.get('root_path', '')):]
        if scope['type'] != 'http' or scope['method'] != 'GET' or path not in READ_PATHS:
            await JSONResponse({'error': 'public_read_only'}, status_code=403)(
                scope, receive, send,
            )
            return
        await self.app(scope, receive, send)


class PrivateMCP:
    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self.token = token.encode() if token and len(token) >= 32 else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.token is None:
            response = JSONResponse({'error': 'mcp_not_configured'}, status_code=503)
        elif not compare_digest(
            Request(scope).headers.get('authorization', '').encode(), b'Bearer ' + self.token,
        ):
            response = JSONResponse({'error': 'unauthorized'}, status_code=401)
        else:
            await self.app(scope, receive, send)
            return
        await response(scope, receive, send)


def create_hosted_app(
    backend: FastAPI, *, static_dir: Path | None = None, access_token: str | None = None
) -> Starlette:
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=backend), trust_env=False)
    adapter = EvidenceClient(client=client)
    server = create_server(adapter, stateless_http=True, json_response=True)
    server.settings.streamable_http_path = '/'
    security = server.settings.transport_security
    if security is not None:
        security.allowed_hosts.append('testserver')
        public_host = environ.get('RAILWAY_PUBLIC_DOMAIN')
        if public_host:
            security.allowed_hosts.append(public_host)
            security.allowed_origins.append('https://' + public_host)
    mcp_app = server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        try:
            async with backend.router.lifespan_context(backend), server.session_manager.run():
                yield
        finally:
            await client.aclose()

    routes = [
        Mount('/api', app=ReadOnlyGateway(backend)),
        Mount('/mcp', app=PrivateMCP(mcp_app, access_token)),
    ]
    if static_dir is not None:
        routes.append(Mount('/', app=StaticFiles(directory=static_dir, html=True)))
    return Starlette(routes=routes, lifespan=lifespan)


def hosted_app() -> Starlette:
    from quant_trading_platform.api.app import app

    return create_hosted_app(
        app, static_dir=Path(environ.get('DASHBOARD_DIST', '/app/frontend-dist')),
        access_token=environ.get('MCP_ACCESS_TOKEN'),
    )

