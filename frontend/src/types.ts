export type PlatformMode = 'RESEARCH' | 'BACKTEST' | 'PAPER' | 'LIVE_LOCKED' | 'LIVE_ENABLED';
export type SafetyState = 'OK' | 'WARNING' | 'PAUSED' | 'STOPPED';
export type RiskDecision = 'Approved' | 'Rejected' | 'Watch';

export interface ExchangeHealth {
  name: string;
  status: 'OK' | 'DELAYED' | 'ERROR';
  latencyMs: number;
}

export interface Opportunity {
  id: string;
  time: string;
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
  event: string;
  strategy: string;
  netEdge?: number;
  decision: string;
  reason: string;
}
