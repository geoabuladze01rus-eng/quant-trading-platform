from decimal import Decimal

import pytest
from test_signal_intelligence import NOW
from test_signal_watch_engine import frame

from quant_trading_platform.signal_watch.collection import ProviderCollector
from quant_trading_platform.signal_watch.engine import WatchEngine
from quant_trading_platform.signal_watch.journal import Journal
from quant_trading_platform.signal_watch.providers import ProviderEvidenceCache
from quant_trading_platform.signal_watch.service import WatchService


@pytest.mark.asyncio
async def test_full_paper_research_cycle_isolates_failures_journals_and_measures_markout(tmp_path):
    """Controlled evidence fixtures, not a claim that vendor transports are deployed."""
    clock, failing = [NOW], set()
    cache = ProviderEvidenceCache()
    domains = {'TraderSpy': 'A', 'CryptoAudit': 'B', 'TradingCursor': 'C',
               'Gina': 'D', 'Exa': 'E', 'Blockscout': 'E'}

    def refresher(source, domain):
        async def collect(symbol):
            if source in failing:
                raise RuntimeError('private details must not escape')
            return cache.update(source, {'symbol': symbol, 'observations': [
                {'domain': domain, 'strength': '0' if source == 'Blockscout' else '1',
                 'timestamp_ms': clock[0], 'origin': f'fixture_{source}'},
            ]}, now_ms=clock[0])
        return collect

    collector = ProviderCollector(cache, {s: refresher(s, d) for s, d in domains.items()},
                                  clock=lambda: clock[0])

    class Source:
        def fetch(self, symbol, *, now_ms):
            return frame(
                timestamp_ms=now_ms,
                close=Decimal('110') if now_ms > NOW else Decimal('102'),
                high=Decimal('111') if now_ms > NOW else Decimal('103'),
            )

        def close(self):
            pass

    with Journal(tmp_path / 'journal.sqlite') as journal:
        service = WatchService(
            WatchEngine(journal), Source(),
            lambda symbol, now: {'quality': {'status': 'insufficient'}},
            external=lambda symbol: cache.observations(symbol, now_ms=clock[0]),
            collect_external=collector.collect, clock=lambda: clock[0],
        )
        await service.poll_once()
        current = service.snapshot()['assets']['BTC/USDT']['candidates'][-1]
        assert current['score'] == '100'
        assert current['confidence'] == 'VERY HIGH'
        assert current['paper_only'] is True and current['live_execution'] is False
        original_id = current['signal_id']
        assert len(journal.candidates()) == 12

        failing.add('Gina')
        await service.poll_once()
        current = service.snapshot()['assets']['BTC/USDT']['candidates'][-1]
        assert current['score'] == '80'
        assert current['confidence'] == 'HIGH'
        assert cache.status('Gina', 'BTC/USDT', now_ms=NOW) == 'error'
        assert cache.status('TraderSpy', 'BTC/USDT', now_ms=NOW) == 'ok'

        failing.add('Exa')
        await service.poll_once()
        current = service.snapshot()['assets']['BTC/USDT']['candidates'][-1]
        assert current['score'] == '65'
        assert current['rejected_reason'] == 'below_confidence_threshold'
        assert any(row['missed_reason'] == 'near_threshold'
                   for row in service.snapshot()['journal']['rows'])
        clock[0] += 900_000
        await service.poll_once()
        markout = next(
            row for row in service.outcomes.snapshot() if row['signal_id'] == original_id
        )
        assert markout['status'] == 'measured'
        assert markout['net_return'] == str(
            Decimal('110') * Decimal('0.9985') / (Decimal('102') * Decimal('1.0015')) - 1,
        )
        assert service.snapshot()['outcomes']['calibration']['VERY HIGH']['samples'] == 3
        assert service.snapshot()['observed_outcomes']['calibration']['VERY HIGH']['samples'] == 0
