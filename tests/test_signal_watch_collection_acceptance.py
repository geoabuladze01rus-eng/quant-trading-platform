import asyncio
import copy
import json
import runpy
from pathlib import Path

import httpx
import pytest
from test_signal_evidence_api import services
from test_signal_watch_readiness import watch

from quant_trading_platform.market_data.liquidations import LiquidationWindow
from quant_trading_platform.market_data.signal_evidence import get_signal_evidence
from quant_trading_platform.signal_watch import readiness
from quant_trading_platform.signal_watch.intelligence import PROVIDERS


async def transport(clock, *, fault=None):
    spot, derivatives = await services(3, 3)
    template = get_signal_evidence('BTC/USDT', spot=spot, derivatives=derivatives,
                                   liquidations=LiquidationWindow(), generated_at_ms=1000)
    calls = []

    def handler(request):
        calls.append(request)
        now = clock[0]
        path = request.url.path.removeprefix('/api')
        if path == '/health':
            return httpx.Response(200, json={'trading_mode': 'paper', 'live_trading': 'locked'})
        if path == '/crypto-signal-watch':
            value = watch()
            value['generated_at_ms'] = now
            for row in value['assets'].values():
                row.update(timestamp_ms=now, valid_until_ms=now + 1000)
            value['providers'] = [
                {'source': source, 'symbol': symbol, 'status': 'ok', 'timestamp_ms': now,
                 'valid_until_ms': now + 1000}
                for source in PROVIDERS - {'Market Structure', 'Data Hub'}
                for symbol in value['assets']
            ]
            return httpx.Response(200, json=value)
        value = copy.deepcopy(template)
        symbol = path.removeprefix('/signal-evidence/')
        value.update(symbol=symbol, generated_at_ms=now)
        for section in ('spot', 'derivatives'):
            for row in value[section]['venues']:
                row['timestamp_ms'] = row['received_at_ms'] = now
                if section == 'derivatives':
                    row.update(symbol=symbol, source_fields=['fixture_public_ticker'],
                               instrument_id=(symbol.replace('/', '-') + '-SWAP'
                                              if row['venue'] == 'okx'
                                              else symbol.replace('/', '')))
        value['liquidation_sources'] = [
            {'venue': venue, 'status': 'connected', 'error': None, 'last_received_at_ms': now}
            for venue in ('binance', 'bybit', 'okx')
        ]
        if fault:
            fault(value, now)
        return httpx.Response(200, json=value)

    return httpx.MockTransport(handler), calls


@pytest.mark.asyncio
async def test_snapshot_retains_validated_original_clocks_without_claiming_continuity(monkeypatch):
    clock = [1_000_000]
    monkeypatch.setattr(readiness, 'time_ns', lambda: clock[0] * 1_000_000)
    wire, _ = await transport(clock)
    async with httpx.AsyncClient(transport=wire) as client:
        report = await readiness.check_readiness('https://example.test', client=client)
    assert report['status'] == 'ready'
    assert len(report['collection_clocks']) == 39
    assert report['collection_clocks']['spot:okx:BTC/USDT'] == 1_000_000
    assert report['collection_clocks']['liquidations:bybit'] == 1_000_000
    assert report['collection_clocks']['provider:Gina:SOL/USDT'] == 1_000_000
    assert report['collection_warnings'] == []
    assert report['continuous_collection_verified'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['future', 'stale', 'duplicate', 'missing', 'error', 'bool',
                                 'unhashable_venue', 'unhashable_status'])
async def test_unverified_websocket_receipts_never_count_as_collection(fault):
    def break_stream(value, now):
        rows = value['liquidation_sources']
        if fault == 'future':
            rows[0]['last_received_at_ms'] = now + 1
        elif fault == 'stale':
            rows[0]['last_received_at_ms'] = now - 40_001
        elif fault == 'duplicate':
            rows[0]['venue'] = 'bybit'
        elif fault == 'missing':
            rows.pop()
        elif fault == 'error':
            rows[0]['status'] = 'error'
        elif fault == 'unhashable_venue':
            rows[0]['venue'] = []
        elif fault == 'unhashable_status':
            rows[0]['status'] = []
        else:
            rows[0]['last_received_at_ms'] = True

    wire, _ = await transport([1_000_000], fault=break_stream)
    async with httpx.AsyncClient(transport=wire) as client:
        report = await readiness.check_readiness('https://example.test', client=client,
                                                now_ms=1_000_000)
    assert report['status'] == 'ready'  # Supporting streams do not change core quality.
    assert report['collection_warnings']
    assert not any(key.startswith('liquidations:') for key in report['collection_clocks'])


@pytest.mark.asyncio
async def test_sampling_proves_progress_over_fifteen_minutes_without_continuity_claim(monkeypatch):
    from quant_trading_platform.signal_watch import acceptance

    clock = [1_000_000]
    monkeypatch.setattr(readiness, 'time_ns', lambda: clock[0] * 1_000_000)
    monkeypatch.setattr(acceptance, 'monotonic_ns', lambda: clock[0] * 1_000_000)
    wire, calls = await transport(clock)

    async def advance(seconds):
        assert seconds == 60
        clock[0] += 60_000

    monkeypatch.setattr(acceptance.asyncio, 'sleep', advance)
    async with httpx.AsyncClient(transport=wire) as client:
        report = await acceptance.observe_collections('https://example.test', client=client,
                                                      samples=16, interval_seconds=60,
                                                      hosted=True)
    assert report['status'] == 'ready'
    assert report['sampled_collection_verified'] is True
    assert report['continuous_collection_verified'] is False
    assert report['elapsed_ms'] == 900_000
    assert report['samples_completed'] == 16
    assert report['progressing_sources'] == 39
    assert len(calls) == 80
    assert all(r.method == 'GET' and r.url.path.startswith('/api/') for r in calls)


@pytest.mark.asyncio
async def test_one_healthy_snapshot_is_not_sustained_acceptance():
    from quant_trading_platform.signal_watch import acceptance

    wire, _ = await transport([1_000_000])
    async with httpx.AsyncClient(transport=wire) as client:
        report = await acceptance.observe_collections('https://example.test', client=client,
                                                      samples=1, interval_seconds=60)
    assert report['sampled_collection_verified'] is False


@pytest.mark.asyncio
async def test_cancellation_stops_sampling_without_more_requests(monkeypatch):
    from quant_trading_platform.signal_watch import acceptance

    wire, calls = await transport([1_000_000])

    async def cancel(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(acceptance.asyncio, 'sleep', cancel)
    async with httpx.AsyncClient(transport=wire) as client:
        with pytest.raises(asyncio.CancelledError):
            await acceptance.observe_collections('https://example.test', client=client,
                                                 samples=2, interval_seconds=60)
    assert len(calls) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['frozen_ws', 'regressed_rest', 'intermittent_ws'])
async def test_bad_sample_cannot_be_repaired_by_later_healthy_sample(monkeypatch, mode):
    from quant_trading_platform.signal_watch import acceptance

    clock = [1_000_000]
    monkeypatch.setattr(readiness, 'time_ns', lambda: clock[0] * 1_000_000)
    monkeypatch.setattr(acceptance, 'monotonic_ns', lambda: clock[0] * 1_000_000)

    def fault(value, now):
        if mode == 'frozen_ws':
            value['liquidation_sources'][0]['last_received_at_ms'] = 1_000_000
        elif mode == 'regressed_rest' and now == 1_060_000:
            value['derivatives']['venues'][0]['timestamp_ms'] = 940_000
        elif mode == 'intermittent_ws' and now == 1_060_000:
            value['liquidation_sources'][0]['status'] = 'error'

    async def advance(_):
        clock[0] += 60_000

    monkeypatch.setattr(acceptance.asyncio, 'sleep', advance)
    wire, _ = await transport(clock, fault=fault)
    async with httpx.AsyncClient(transport=wire) as client:
        report = await acceptance.observe_collections('https://example.test', client=client,
                                                      samples=16, interval_seconds=60)
    assert report['sampled_collection_verified'] is False
    if mode == 'regressed_rest':
        assert 'collection_clock_regressed:derivatives:binance:BTC/USDT' in report['warnings']
    else:
        assert 'collection_receipts_unverified' in report['warnings']


@pytest.mark.asyncio
@pytest.mark.parametrize('samples,interval', [(True, 60), (0, 60), (242, 15),
                                            (16, 61), (16, 14), (62, 60)])
async def test_invalid_sampling_schedule_is_refused_before_network(samples, interval):
    from quant_trading_platform.signal_watch import acceptance

    wire = httpx.MockTransport(lambda r: pytest.fail('Invalid schedule contacted backend'))
    async with httpx.AsyncClient(transport=wire) as client:
        with pytest.raises(ValueError):
            await acceptance.observe_collections('https://example.test', client=client,
                                                 samples=samples, interval_seconds=interval)


@pytest.mark.asyncio
async def test_cli_emits_incomplete_sampled_report_using_hosted_gets(monkeypatch, capsys):
    script = runpy.run_path(str(Path(__file__).parents[1] /
                               'scripts/check_signal_watch_readiness.py'))
    wire, calls = await transport([1_000_000])
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient',
                        lambda **kw: original_client(transport=wire, **kw))
    assert await script['run']('https://example.test', hosted=True, samples=1,
                               interval_seconds=60) is False
    report = json.loads(capsys.readouterr().out)
    assert report['scope'] == 'read_only_sampled_collection_acceptance'
    assert report['continuous_collection_verified'] is False
    assert len(calls) == 5
    assert all(r.url.path.startswith('/api/') for r in calls)
