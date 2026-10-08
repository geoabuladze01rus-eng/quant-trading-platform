"""Bounded GET-only sampled collection acceptance; gaps remain explicitly unverified."""

import asyncio
from time import monotonic_ns

import httpx

from quant_trading_platform.market_data.derivatives import SIGNAL_SYMBOLS
from quant_trading_platform.signal_watch.intelligence import PROVIDERS
from quant_trading_platform.signal_watch.readiness import check_readiness


async def observe_collections(
    origin: str, *, client: httpx.AsyncClient, samples: int = 16,
    interval_seconds: int = 60, hosted: bool = False,
) -> dict[str, object]:
    if (type(samples) is not int or not 1 <= samples <= 241
        or type(interval_seconds) is not int or not 15 <= interval_seconds <= 60
        or (samples - 1) * interval_seconds > 3600):
        raise ValueError('Invalid bounded acceptance schedule')
    expected = {
        f'{section}:{venue}:{symbol}'
        for section in ('spot', 'derivatives') for venue in ('binance', 'bybit', 'okx')
        for symbol in SIGNAL_SYMBOLS
    } | {
        f'provider:{source}:{symbol}'
        for source in PROVIDERS - {'Market Structure', 'Data Hub'} for symbol in SIGNAL_SYMBOLS
    } | {f'liquidations:{venue}' for venue in ('binance', 'bybit', 'okx')}
    first: dict[str, int] = {}
    previous: dict[str, int] = {}
    progressed: set[str] = set()
    warnings: set[str] = set()
    reports: list[dict[str, object]] = []
    started = monotonic_ns()
    for index in range(samples):
        if index:
            await asyncio.sleep(interval_seconds)
        report = await check_readiness(origin, client=client, hosted=hosted)
        reports.append(report)
        if report['status'] != 'ready':
            warnings.add('snapshot_acceptance_incomplete')
        if report['collection_warnings']:
            warnings.add('collection_receipts_unverified')
        clocks = report['collection_clocks']
        assert isinstance(clocks, dict)
        if set(clocks) != expected:
            warnings.add('required_collection_clocks_missing')
        for key in expected & clocks.keys():
            timestamp = clocks[key]
            if type(timestamp) is not int:
                warnings.add('invalid_collection_clock')
                continue
            if timestamp < previous.get(key, timestamp):
                warnings.add('collection_clock_regressed:' + key)
            if timestamp > first.setdefault(key, timestamp):
                progressed.add(key)
            previous[key] = timestamp
    elapsed_ms = (monotonic_ns() - started) // 1_000_000
    if elapsed_ms < 900_000:
        warnings.add('observation_shorter_than_fifteen_minutes')
    if progressed != expected:
        warnings.add('collection_progress_unverified')
    verified = not warnings
    return {
        'scope': 'read_only_sampled_collection_acceptance',
        'status': 'ready' if verified else 'incomplete',
        'samples_completed': len(reports), 'interval_seconds': interval_seconds,
        'elapsed_ms': elapsed_ms, 'progressing_sources': len(progressed),
        'sampled_collection_verified': verified, 'continuous_collection_verified': False,
        'warnings': sorted(warnings), 'samples': reports,
    }
