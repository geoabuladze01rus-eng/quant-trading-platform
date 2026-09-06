import { mockAudit, mockOpportunities, mockRisk, mockSettings, mockVenues } from './mockData';
import type { AuditEvent, DashboardSettings, Opportunity, OpportunityResponse, Risk, Venue } from './types';

const baseUrl = import.meta.env.VITE_API_BASE_URL?.trim().replace(/\/+$/, '');
export const isMockMode = !baseUrl;

// Validate untrusted JSON before rendering financial metrics or safety state.
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid API object');
  return value as Record<string, unknown>;
}
function string(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Invalid API text');
  return value;
}
function number(value: unknown): number {
  if (typeof value !== 'number' && (typeof value !== 'string' || !value.trim())) {
    throw new Error('Invalid API number');
  }
  const result = Number(value);
  if (!Number.isFinite(result)) throw new Error('Invalid API number');
  return result;
}
function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('Invalid API boolean');
  return value;
}
function list<T>(value: unknown, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new Error('Invalid API list');
  return value.map(parse);
}

async function request<T>(path: string, mock: T, parse: (value: unknown) => T): Promise<T> {
  if (isMockMode) return mock;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      signal: controller.signal, credentials: 'omit', cache: 'no-store',
    });
    if (!response.ok || response.status === 204) throw new Error('Missing API data');
    return parse(await response.json());
  } catch {
    // Do not expose server payloads, request URLs or credentials in error messages.
    throw new Error(`${path}: backend unavailable or invalid response. Data is not trusted.`);
  } finally {
    clearTimeout(timeout);
  }
}

export function getSettings(): Promise<DashboardSettings> {
  return request('/settings', mockSettings, (value) => {
    const v = record(value);
    return {
      trading_mode: string(v.trading_mode), market_scope: string(v.market_scope),
      live_trading_enabled: boolean(v.live_trading_enabled), t_invest_sandbox: boolean(v.t_invest_sandbox),
      max_daily_loss_pct: number(v.max_daily_loss_pct),
      max_trade_notional_usd: number(v.max_trade_notional_usd),
      min_expected_net_pct: number(v.min_expected_net_pct),
    };
  });
}
export function getVenues(): Promise<Venue[]> {
  return request('/venues', mockVenues, (value) => list(value, (item) => {
    const v = record(item);
    return { name: string(v.name), market: string(v.market), status: string(v.status), live_execution: boolean(v.live_execution) };
  }));
}
function parseOpportunity(value: unknown): Opportunity {
  const v = record(value);
  return {
    strategy: string(v.strategy), symbol: string(v.symbol),
    buy_venue: string(v.buy_venue), sell_venue: string(v.sell_venue),
    gross_spread_pct: number(v.gross_spread_pct), fees_pct: number(v.fees_pct),
    slippage_pct: number(v.slippage_pct), expected_net_pct: number(v.expected_net_pct),
    max_notional_usd: number(v.max_notional_usd), approved: boolean(v.approved),
    reason: string(v.reason), data_age_ms: number(v.data_age_ms),
  };
}
export function getOpportunities(): Promise<OpportunityResponse> {
  return request('/opportunities', mockOpportunities, (value) => {
    const v = record(value);
    if (v.status !== 'ok' && v.status !== 'no_data') throw new Error('Invalid API status');
    const opportunities = list(v.opportunities, parseOpportunity);
    if (v.status === 'no_data' && opportunities.length) throw new Error('Inconsistent API status');
    return { status: v.status, opportunities };
  });
}
export function getRisk(): Promise<Risk> {
  return request('/risk', mockRisk, (value) => {
    const v = record(value);
    return {
      max_daily_loss_pct: number(v.max_daily_loss_pct),
      max_trade_notional_usd: number(v.max_trade_notional_usd),
      min_expected_net_pct: number(v.min_expected_net_pct),
      live_trading_locked: boolean(v.live_trading_locked),
      stale_data_protection: boolean(v.stale_data_protection),
      api_error_protection: boolean(v.api_error_protection),
      balance_mismatch_protection: boolean(v.balance_mismatch_protection),
    };
  });
}
export function getAudit(): Promise<AuditEvent[]> {
  return request('/audit', mockAudit, (value) => list(value, (item) => {
    const v = record(item);
    return {
      id: string(v.id), timestamp: string(v.timestamp), market_scope: string(v.market_scope),
      event: string(v.event), strategy: string(v.strategy), reason: string(v.reason),
    };
  }));
}
