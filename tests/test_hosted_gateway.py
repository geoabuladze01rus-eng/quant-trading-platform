from fastapi import FastAPI
from starlette.testclient import TestClient

from quant_trading_platform.mcp.hosted import create_hosted_app


def backend():
    app = FastAPI()
    app.state.writes = 0

    @app.get('/health')
    def health():
        return {'status': 'ok', 'live_trading': 'locked'}

    @app.post('/paper/orders/simulate')
    def write():
        app.state.writes += 1
        return {'written': True}

    @app.get('/secret')
    def secret():
        return {'secret': 'must not escape'}

    return app


def test_public_gateway_reads_health_but_never_dispatches_commands(tmp_path):
    app = backend()
    (tmp_path / 'index.html').write_text('<html>dashboard</html>')
    with TestClient(create_hosted_app(app, static_dir=tmp_path)) as client:
        assert client.get('/api/health').json()['live_trading'] == 'locked'
        assert 'dashboard' in client.get('/').text
        for path in ['/api/paper/orders/simulate', '/api/health', '/api/secret']:
            assert client.post(path).status_code == 403
        assert client.get('/api/secret').status_code == 403
        assert client.get('/api/docs').status_code == 403
        assert client.get('/api/paper/orders/simulate').status_code == 403
        assert client.get('/paper/orders/simulate').status_code == 404
    assert app.state.writes == 0


def initialize():
    return {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-06-18', 'capabilities': {},
        'clientInfo': {'name': 'gateway-test', 'version': '1'},
    }}


def test_http_mcp_requires_token_and_discovers_only_evidence_tool():
    token = 'test-only-access-token-long-enough-123456789'
    with TestClient(create_hosted_app(backend(), access_token=token)) as client:
        headers = {'Accept': 'application/json, text/event-stream'}
        assert client.post('/mcp/', json=initialize(), headers=headers).status_code == 401
        headers['Authorization'] = 'Bearer wrong-token'
        assert client.post('/mcp/', json=initialize(), headers=headers).status_code == 401
        headers['Authorization'] = 'Bearer ' + token
        reply = client.post('/mcp/', json=initialize(), headers=headers)
        assert reply.status_code == 200
        assert reply.json()['result']['serverInfo']['name'] == 'Crypto Signal Data Hub'
        reply = client.post('/mcp/', json={
            'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {},
        }, headers=headers)
        assert [tool['name'] for tool in reply.json()['result']['tools']] == [
            'get_signal_evidence',
        ]


def test_missing_or_short_token_keeps_mcp_closed():
    for token in [None, '', 'short']:
        with TestClient(create_hosted_app(backend(), access_token=token)) as client:
            assert client.post('/mcp/', json=initialize()).status_code == 503


def test_hosted_tool_uses_real_backend_and_reports_missing_data(monkeypatch, tmp_path):
    from importlib import import_module

    api = import_module('quant_trading_platform.api.app')
    monkeypatch.setattr(api.settings, 'public_market_data_enabled', False)
    monkeypatch.setattr(api.settings, 'okx_spot_auto_enabled', False)
    monkeypatch.setattr(api.settings, 'paper_database_path', tmp_path / 'paper.sqlite3')
    token = 'test-only-private-access-token-1234567890'
    with TestClient(create_hosted_app(api.app, access_token=token)) as client:
        headers = {'Accept': 'application/json, text/event-stream',
                   'Authorization': 'Bearer ' + token}
        reply = client.post('/mcp/', headers=headers, json={
            'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
            'params': {'name': 'get_signal_evidence', 'arguments': {'symbol': 'BTC/USDT'}},
        })
        evidence = reply.json()['result']['structuredContent']
        assert evidence['symbol'] == 'BTC/USDT'
        assert evidence['quality']['status'] == 'insufficient'
        assert evidence['execution'] == {'paper_only': True, 'live_execution': False}
