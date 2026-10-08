import asyncio
import sqlite3
from decimal import Decimal

import pytest
from test_signal_intelligence import NOW
from test_signal_watch_engine import frame

from quant_trading_platform.signal_watch.engine import WatchEngine
from quant_trading_platform.signal_watch.journal import Journal
from quant_trading_platform.signal_watch.service import WatchService


def test_provider_snapshot_exposes_earliest_original_source_deadline():
    from quant_trading_platform.signal_watch.providers import ProviderEvidenceCache

    cache = ProviderEvidenceCache()
    assert cache.update('Gina', {'symbol': 'BTC/USDT', 'observations': [
        {'domain': 'D', 'strength': '0.6', 'timestamp_ms': NOW - 4_000,
         'origin': 'hyperliquid_canonical_usdc_depth'},
    ]}, now_ms=NOW)
    row = next(r for r in cache.snapshot(now_ms=NOW)
               if r['source'] == 'Gina' and r['symbol'] == 'BTC/USDT')
    assert row['valid_until_ms'] == NOW + 1_000
    assert row['timestamp_ms'] == NOW - 4_000


def test_recent_diagnostics_are_bounded_newest_first_with_rejection_and_missed_reason(tmp_path):
    with Journal(tmp_path / 'journal.sqlite') as journal:
        engine = WatchEngine(journal)
        for offset in range(30):
            engine.scan(
                'BTC/USDT', frame(timestamp_ms=NOW + offset), (),
                data_hub_quality='insufficient', now_ms=NOW + offset, market_regime='trend',
            )
        rows = engine.recent_diagnostics()
        assert len(rows) == 100
        assert rows[0]['timestamp_ms'] == NOW + 29
        assert rows[-1]['timestamp_ms'] == NOW + 5
        assert rows[0]['setup'] == 'Momentum'
        assert rows[0]['rejected_reason'] == 'insufficient_independent_evidence'
        assert rows[0]['missed_reason'] == 'insufficient_independent_evidence'
        assert rows[1]['missed_reason'] is None
        assert all(len(row['signal_id']) == 64 for row in rows)
        assert all(isinstance(row['score'], str) for row in rows)
        assert len(journal.candidates()) == 120


@pytest.mark.asyncio
async def test_cached_history_survives_expiry_and_never_reads_sqlite_from_get(tmp_path):
    clock = [NOW]

    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame(timestamp_ms=now_ms)

        def close(self):
            pass

    with Journal(tmp_path / 'journal.sqlite') as journal:
        service = WatchService(
            WatchEngine(journal), Source(), lambda symbol, now: {}, clock=lambda: clock[0],
        )
        await service.poll_once()
        journal.connection.set_authorizer(lambda *args: sqlite3.SQLITE_DENY)
        state = await asyncio.to_thread(service.snapshot)
        assert state['generated_at_ms'] == NOW
        assert state['journal']['kind'] == 'historical_candidates'
        assert len(state['journal']['rows']) == 12
        state['journal']['rows'].clear()
        clock[0] += 60_001
        expired = await asyncio.to_thread(service.snapshot)
        assert len(expired['journal']['rows']) == 12
        assert expired['assets']['BTC/USDT']['status'] == 'stale'
        assert expired['assets']['BTC/USDT']['candidates'] == []
        journal.connection.set_authorizer(None)
        assert len(journal.candidates()) == 12


@pytest.mark.asyncio
async def test_restart_restores_history_and_separate_observed_calibration(tmp_path):
    path = tmp_path / 'journal.sqlite'
    with Journal(path) as journal:
        engine = WatchEngine(journal)
        candidate = engine.scan(
            'BTC/USDT', frame(), (), data_hub_quality='insufficient', now_ms=NOW,
            market_regime='trend',
        )[-1]
        engine.record_outcome(
            candidate.signal_id, net_return=Decimal('0.0123'), timestamp_ms=NOW + 1,
        )
    with Journal(path) as journal:
        service = WatchService(
            WatchEngine(journal), None, lambda symbol, now: {}, clock=lambda: NOW,
        )
        state = service.snapshot()
        assert len(state['journal']['rows']) == 4
        assert state['observed_outcomes']['kind'] == 'observed_net_return'
        assert state['observed_outcomes']['calibration']['REJECTED'] == {
            'samples': 1, 'positive': 1, 'mean_net_return': '0.0123',
        }
        assert state['outcomes']['calibration']['REJECTED']['samples'] == 0
