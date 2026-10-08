import httpx
import pytest
from test_signal_intelligence import NOW

from quant_trading_platform.signal_watch.readiness import check_readiness


def watch():
    return {'generated_at_ms': NOW, 'paper_only': True, 'live_execution': False,
            'assets': {s: {'status': 'ok', 'timestamp_ms': NOW, 'valid_until_ms': NOW + 1_000,
                           'candidates': []} for s in ('BTC/USDT', 'ETH/USDT', 'SOL/USDT')},
            'providers': [], 'journal': {'kind': 'historical_candidates', 'limit': 100, 'rows': []},
            'outcomes': {'kind': 'estimated_forward_markout', 'calibration': {}},
            'observed_outcomes': {'kind': 'observed_net_return', 'calibration': {}}}


@pytest.mark.asyncio
async def test_probe_is_get_only_keyless_and_missing_sources_prevent_full_readiness():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == '/health':
            return httpx.Response(200, json={'trading_mode': 'paper', 'live_trading': 'locked'})
        if request.url.path == '/crypto-signal-watch':
            return httpx.Response(200, json=watch())
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 headers={'Authorization': 'must-not-leave'},
                                 cookies={'session': 'must-not-leave'}) as client:
        report = await check_readiness('https://example.test', client=client, now_ms=NOW)
    assert report['status'] == 'incomplete'
    assert report['engine_available'] is True
    assert report['missing_providers'] == [
        'Blockscout', 'CryptoAudit', 'Exa', 'Gina', 'TraderSpy', 'TradingCursor',
    ]
    assert len(requests) == 5
    assert all(r.method == 'GET' for r in requests)
    assert all('authorization' not in r.headers and 'cookie' not in r.headers for r in requests)
    assert any(str(r.url).endswith('/signal-evidence/BTC%2FUSDT') for r in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'bad', ['live', 'missing_watch', 'future_watch', 'stale_watch', 'redirect'],
)
async def test_probe_fails_closed_on_unsafe_missing_or_unfresh_backend(bad):
    def handler(request):
        if request.url.path == '/health':
            return httpx.Response(200, json={'trading_mode': 'live' if bad == 'live' else 'paper',
                                             'live_trading': 'locked'})
        if request.url.path == '/crypto-signal-watch':
            if bad == 'missing_watch':
                return httpx.Response(404)
            if bad == 'redirect':
                return httpx.Response(302, headers={'Location': 'https://private.example'})
            value = watch()
            if bad == 'future_watch':
                value['generated_at_ms'] = NOW + 1
            if bad == 'stale_watch':
                value['assets']['BTC/USDT']['valid_until_ms'] = NOW - 1
            return httpx.Response(200, json=value)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check_readiness('https://example.test', client=client, now_ms=NOW)
    assert report['status'] == 'incomplete'
    assert report['engine_available'] is False


@pytest.mark.asyncio
async def test_probe_refuses_credentials_paths_and_remote_plain_http():
    transport = httpx.MockTransport(lambda r: pytest.fail('request'))
    async with httpx.AsyncClient(transport=transport) as c:
        for origin in ('https://user:password@example.test', 'https://example.test/private',
                       'http://example.test', 'https://example.test?token=private'):
            with pytest.raises(ValueError):
                await check_readiness(origin, client=c, now_ms=NOW)


@pytest.mark.asyncio
async def test_probe_uses_receipt_clock_after_network_delay(monkeypatch):
    from quant_trading_platform.signal_watch import readiness

    clock = [NOW]
    monkeypatch.setattr(readiness, 'time_ns', lambda: clock[0] * 1_000_000)

    def handler(request):
        if request.url.path == '/health':
            return httpx.Response(200, json={'trading_mode': 'paper', 'live_trading': 'locked'})
        if request.url.path == '/crypto-signal-watch':
            clock[0] += 500
            value = watch()
            value['generated_at_ms'] = clock[0]
            return httpx.Response(200, json=value)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check_readiness('https://example.test', client=client)
    assert report['engine_available'] is True


@pytest.mark.asyncio
async def test_probe_rechecks_watch_expiry_after_remaining_evidence_requests(monkeypatch):
    from quant_trading_platform.signal_watch import readiness

    clock = [NOW]
    monkeypatch.setattr(readiness, 'time_ns', lambda: clock[0] * 1_000_000)

    def handler(request):
        if request.url.path == '/health':
            return httpx.Response(200, json={'trading_mode': 'paper', 'live_trading': 'locked'})
        if request.url.path == '/crypto-signal-watch':
            return httpx.Response(200, json=watch())
        clock[0] += 500
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check_readiness('https://example.test', client=client)
    assert report['engine_available'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('delay,status,early_quality', [
    (900, 'incomplete', 'stale_after_probe'), (100, 'ready', 'healthy'),
])
async def test_ready_requires_all_evidence_still_fresh_at_probe_completion(
    monkeypatch, delay, status, early_quality,
):
    import copy

    from test_signal_evidence_api import services

    from quant_trading_platform.market_data.liquidations import LiquidationWindow
    from quant_trading_platform.market_data.signal_evidence import get_signal_evidence
    from quant_trading_platform.signal_watch import readiness
    from quant_trading_platform.signal_watch.intelligence import PROVIDERS

    spot, derivatives = await services(3, 3)
    original = get_signal_evidence('BTC/USDT', spot=spot, derivatives=derivatives,
                                  liquidations=LiquidationWindow(), generated_at_ms=1000)
    clock = [1000]
    monkeypatch.setattr(readiness, 'time_ns', lambda: clock[0] * 1_000_000)
    value = watch()
    value['generated_at_ms'] = 1000
    for state in value['assets'].values():
        state.update(timestamp_ms=1000, valid_until_ms=61_000)
    value['providers'] = [
        {'source': source, 'symbol': symbol, 'status': 'ok', 'valid_until_ms': 61_000}
        for source in PROVIDERS - {'Market Structure', 'Data Hub'}
        for symbol in value['assets']
    ]

    def handler(request):
        if request.url.path == '/health':
            return httpx.Response(200, json={'trading_mode': 'paper', 'live_trading': 'locked'})
        if request.url.path == '/crypto-signal-watch':
            return httpx.Response(200, json=value)
        payload = copy.deepcopy(original)
        symbol = request.url.path.removeprefix('/signal-evidence/')
        payload['symbol'], payload['generated_at_ms'] = symbol, clock[0]
        for section in ('spot', 'derivatives'):
            for row in payload[section]['venues']:
                row['timestamp_ms'] = row['received_at_ms'] = clock[0]
                if section == 'derivatives':
                    row['source_fields'] = ['fixture_public_ticker']
                    row['symbol'] = symbol
                    row['instrument_id'] = (symbol.replace('/', '-') + '-SWAP'
                                            if row['venue'] == 'okx' else symbol.replace('/', ''))
        clock[0] += delay
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check_readiness('https://example.test', client=client)
    assert report['status'] == status
    assert report['evidence_quality']['BTC/USDT'] == early_quality
    assert report['evidence_quality']['ETH/USDT'] == early_quality
    assert report['evidence_quality']['SOL/USDT'] == 'healthy'
