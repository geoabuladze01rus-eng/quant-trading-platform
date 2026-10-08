"""Credential-free snapshot acceptance. Never deployment or continuous-operation proof."""

import asyncio
import json
from decimal import Decimal
from time import time_ns

import httpx

from quant_trading_platform.market_data.derivatives import SIGNAL_SYMBOLS
from quant_trading_platform.mcp.evidence_client import EvidenceClient
from quant_trading_platform.signal_watch.intelligence import PROVIDERS


async def _get(client: httpx.AsyncClient, origin: str, path: str) -> dict[str, object]:
    request = httpx.Request('GET', origin + path,
                            extensions={'timeout': httpx.Timeout(5).as_dict()})
    async with asyncio.timeout(5):
        response = await client.send(request, auth=None, follow_redirects=False, stream=True)
        try:
            if response.status_code != 200:
                raise ValueError('Unavailable snapshot')
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 1_048_576:
                    raise ValueError('Oversize snapshot')
                body.extend(chunk)
            value = json.loads(body, parse_float=Decimal)
            if not isinstance(value, dict):
                raise ValueError('Missing snapshot')
            return value
        finally:
            await response.aclose()


def _watch_available(value: dict[str, object], now: int) -> bool:
    generated, assets = value.get('generated_at_ms'), value.get('assets')
    if (
        value.get('paper_only') is not True or value.get('live_execution') is not False
        or type(generated) is not int or not 0 <= now - generated <= 60_000
        or not isinstance(assets, dict) or set(assets) != set(SIGNAL_SYMBOLS)
    ):
        return False
    for asset in assets.values():
        if not isinstance(asset, dict):
            return False
        timestamp, deadline = asset.get('timestamp_ms'), asset.get('valid_until_ms')
        if (asset.get('status') != 'ok' or type(timestamp) is not int
            or type(deadline) is not int or not 0 <= now - timestamp <= 60_000
            or deadline < now):
            return False
    return True


def _evidence_fresh(value: dict[str, object], now: int) -> bool:
    """Recheck clocks of a packet already validated by EvidenceClient."""
    generated = value.get('generated_at_ms')
    if type(generated) is not int or not 0 <= now - generated <= 5_000:
        return False
    for section, limit in (('spot', 1_000), ('derivatives', 360_000)):
        data = value.get(section)
        if not isinstance(data, dict) or not isinstance(data.get('venues'), list):
            return False
        for row in data['venues']:
            if not isinstance(row, dict):
                return False
            timestamp = row.get('timestamp_ms')
            if type(timestamp) is not int or not 0 <= now - timestamp <= limit:
                return False
    return True


async def check_readiness(
    origin: str, *, client: httpx.AsyncClient, now_ms: int | None = None,
) -> dict[str, object]:
    if now_ms is not None and (type(now_ms) is not int or now_ms <= 0):
        raise ValueError('Invalid acceptance clock')
    def clock() -> int:
        return time_ns() // 1_000_000 if now_ms is None else now_ms

    # Reuse the evidence bridge's origin, Decimal, identity and freshness validation.
    evidence = EvidenceClient(origin, client=client, clock=clock)
    external = PROVIDERS - {'Market Structure', 'Data Hub'}
    health: dict[str, object] = {}
    watch: dict[str, object] = {}
    warnings: list[str] = []
    for path, target in (('/health', health), ('/crypto-signal-watch', watch)):
        try:
            target.update(await _get(client, evidence.origin, path))
        except Exception:
            warnings.append('health_unavailable' if path == '/health' else 'watch_unavailable')
    locked = health.get('trading_mode') == 'paper' and health.get('live_trading') == 'locked'
    received_at = clock()
    engine_available = locked and _watch_available(watch, received_at)
    if not locked:
        warnings.append('paper_live_lock_unverified')
    if not engine_available:
        warnings.append('watch_not_fresh_or_disabled')
    missing = set(external)
    rows = watch.get('providers')
    if isinstance(rows, list):
        for source in external:
            states = [row for row in rows if isinstance(row, dict) and row.get('source') == source]
            if (
                len(states) == 3
                and {row.get('symbol') for row in states} == set(SIGNAL_SYMBOLS)
                and all(row.get('status') == 'ok' and type(row.get('valid_until_ms')) is int
                        and row['valid_until_ms'] >= received_at for row in states)
            ):
                missing.discard(source)
    qualities = {}
    snapshots: dict[str, dict[str, object]] = {}
    for symbol in SIGNAL_SYMBOLS:
        try:
            snapshot = await evidence.get_signal_evidence(symbol)
            snapshots[symbol] = snapshot
            quality = snapshot.get('quality')
            qualities[symbol] = (
                quality.get('status') if isinstance(quality, dict) else 'unavailable'
            )
        except Exception:
            qualities[symbol] = 'unavailable'
    ready = engine_available and not missing and all(q == 'healthy' for q in qualities.values())
    # The other probes can outlive a five-second/spot evidence deadline.
    finished_at = clock()
    for symbol, snapshot in snapshots.items():
        if not _evidence_fresh(snapshot, finished_at):
            qualities[symbol] = 'stale_after_probe'
            warnings.append('stale_evidence_at_completion:' + symbol)
    engine_available = locked and _watch_available(watch, finished_at)
    if not engine_available and 'watch_not_fresh_or_disabled' not in warnings:
        warnings.append('watch_not_fresh_or_disabled')
    if isinstance(rows, list):
        for source in external - missing:
            states = [row for row in rows if isinstance(row, dict) and row.get('source') == source]
            if any(type(row.get('valid_until_ms')) is not int
                   or row['valid_until_ms'] < finished_at for row in states):
                missing.add(source)
    ready = (ready and engine_available and not missing
             and all(q == 'healthy' for q in qualities.values()))
    return {
        'scope': 'read_only_snapshot_acceptance', 'status': 'ready' if ready else 'incomplete',
        'engine_available': engine_available, 'paper_live_lock_verified': locked,
        'missing_providers': sorted(missing), 'evidence_quality': qualities,
        'warnings': warnings, 'continuous_collection_verified': False,
    }
