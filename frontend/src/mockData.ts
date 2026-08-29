import type { AuditEvent, ExchangeHealth, Opportunity, PlatformMode, SafetyState } from './types';

export const platformMode: PlatformMode = 'PAPER';
export const safetyState: SafetyState = 'OK';

export const exchangeHealth: ExchangeHealth[] = [
  { name: 'Binance', status: 'OK', latencyMs: 94 },
  { name: 'Bybit', status: 'OK', latencyMs: 121 },
  { name: 'OKX', status: 'DELAYED', latencyMs: 318 },
];

export const opportunities: Opportunity[] = [
  {
    id: 'opp-1',
    time: '12:01:02',
    strategy: 'Triangular',
    path: 'BTC/USDT -> ETH/BTC -> ETH/USDT',
    buyVenue: 'Binance',
    sellVenue: 'Binance',
    grossPct: 0.34,
    feesPct: 0.08,
    slippagePct: 0.05,
    netPct: 0.21,
    notionalUsd: 1000,
    dataAgeMs: 92,
    decision: 'Approved',
    reason: 'Paper trading only',
  },
  {
    id: 'opp-2',
    time: '12:02:14',
    strategy: 'Spread',
    path: 'ETH/USDT',
    buyVenue: 'OKX',
    sellVenue: 'Bybit',
    grossPct: 0.16,
    feesPct: 0.05,
    slippagePct: 0.04,
    netPct: 0.07,
    notionalUsd: 700,
    dataAgeMs: 318,
    decision: 'Rejected',
    reason: 'Below minimum edge',
  },
  {
    id: 'opp-3',
    time: '12:04:41',
    strategy: 'Funding',
    path: 'BTC spot/perp basis',
    buyVenue: 'Binance',
    sellVenue: 'Bybit',
    grossPct: 0.22,
    feesPct: 0.07,
    slippagePct: 0.03,
    netPct: 0.12,
    notionalUsd: 500,
    dataAgeMs: 144,
    decision: 'Watch',
    reason: 'Strategy disabled for MVP',
  },
];

export const auditEvents: AuditEvent[] = [
  {
    id: 'log-1',
    time: '12:01:02',
    event: 'Signal',
    strategy: 'Triangular',
    netEdge: 0.21,
    decision: 'Approved',
    reason: 'Passed paper trading risk checks',
  },
  {
    id: 'log-2',
    time: '12:02:14',
    event: 'Rejected',
    strategy: 'Spread',
    netEdge: 0.07,
    decision: 'Rejected',
    reason: 'Expected net edge below threshold',
  },
  {
    id: 'log-3',
    time: '12:03:30',
    event: 'Health check',
    strategy: 'System',
    decision: 'Warning',
    reason: 'OKX data latency above normal range',
  },
];
