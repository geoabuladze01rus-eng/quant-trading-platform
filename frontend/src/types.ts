export type PlatformMode = 'RESEARCH' | 'BACKTEST' | 'PAPER' | 'LIVE_LOCKED' | 'LIVE_ENABLED';
export type MarketScope = 'CRYPTO' | 'RUSSIAN_STOCKS' | 'MIXED';
export type SafetyState = 'OK' | 'WARNING' | 'PAUSED' | 'STOPPED';
export type RiskDecision = 'Approved' | 'Rejected' | 'Watch';

export interface ExchangeHealth {
  name: string;
  group: 'Crypto' | 'Russian market';
  status: 'OK' | 'DELAYED' | 'ERROR' | 'SANDBOX';
  latencyMs: number;
}

export interface Opportunity {
  id: string;
  time: string;
  market: 'Crypto' | 'Russian market';
  strategy: string;
  path: string;
  buyVenue: string;
  sellVenue: string;
  grossPct: number;
  feesPct: number;
  slippagePct: number;
  netPct: number;
  notionalUsd: number;
  dataAgeMs: number;
  decision: RiskDecision;
  reason: string;
}

export interface AuditEvent {
  id: string;
  time: string;
  market: 'Crypto' | 'Russian market' | 'System';
  event: string;
  strategy: string;
  netEdge?: number;
  decision: string;
  reason: string;
}
