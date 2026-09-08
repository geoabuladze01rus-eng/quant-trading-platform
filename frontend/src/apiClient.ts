import { mockAudit, mockOpportunities, mockRisk, mockSettings, mockVenues } from './mockData';
import type { AuditEvent, DashboardSettings, Opportunity, OpportunityResponse, PaperRecord, PaperReport, Risk, Venue } from './types';

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
function nullableString(value: unknown): string | null {
  return value === null ? null : string(value);
}
function price(value: unknown): string | null {
  if (value === null) return null;
  const result = string(value);
  if (number(result) <= 0) throw new Error('Invalid API price');
  return result;
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
      max_market_data_age_ms: v.max_market_data_age_ms === undefined ? 1000 : number(v.max_market_data_age_ms),
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
    if (v.mode !== 'public_read_only' && v.mode !== 'sandbox' && v.mode !== 'disabled') throw new Error('Invalid source mode');
    if (v.status !== 'ok' && v.status !== 'no_data' && v.status !== 'stale' && v.status !== 'error' && v.status !== 'disabled') throw new Error('Invalid source status');
    const age = v.data_age_ms === null ? null : number(v.data_age_ms);
    if (age !== null && age < 0) throw new Error('Invalid source age');
    const bid = price(v.bid);
    const ask = price(v.ask);
    if ((bid === null) !== (ask === null) || (bid !== null && ask !== null && Number(bid) > Number(ask))) throw new Error('Invalid order book');
    if (v.status === 'ok' && (bid === null || age === null)) throw new Error('Missing source data');
    return {
      name: string(v.name), market: string(v.market), mode: v.mode, status: v.status,
      depth_status: string(v.depth_status), bid_levels: number(v.bid_levels), ask_levels: number(v.ask_levels),
      live_execution: boolean(v.live_execution), symbol: string(v.symbol),
      data_age_ms: age, error: nullableString(v.error), bid, ask,
      timestamp_source: nullableString(v.timestamp_source),
    };
  }));
}
function parseOpportunity(value: unknown): Opportunity {
  const v = record(value);
  const score = v.risk_score;
  if ((score !== 'passed' && score !== 'blocked') || number(v.data_age_ms) < 0 || number(v.simulation_notional_usd) <= 0) throw new Error('Invalid risk metrics');
  return {
    id: string(v.id), summary: string(v.summary), reason_code: string(v.reason_code),
    reason_text: string(v.reason_text), risk_score: score,
    simulation_notional_usd: string(v.simulation_notional_usd),
    strategy: string(v.strategy), symbol: string(v.symbol),
    buy_venue: string(v.buy_venue), sell_venue: string(v.sell_venue),
    gross_spread_pct: number(v.gross_spread_pct), fees_pct: number(v.fees_pct),
    slippage_pct: number(v.slippage_pct), expected_net_pct: number(v.expected_net_pct),
    max_notional_usd: number(v.max_notional_usd), approved: boolean(v.approved),
    reason: string(v.reason), data_age_ms: number(v.data_age_ms),
  };
}
export function getOpportunities(): Promise<OpportunityResponse> {
  return request('/opportunities?explain=true', mockOpportunities, (value) => {
    const v = record(value);
    if (v.status !== 'ok' && v.status !== 'no_data') throw new Error('Invalid API status');
    const opportunities = list(v.opportunities, parseOpportunity);
    if (v.status === 'no_data' && opportunities.length) throw new Error('Inconsistent API status');
    return { status: v.status, opportunities };
  });
}

function parsePaperRecord(value: unknown): PaperRecord {
  const v = record(value);
  for (const field of Object.values(v)) {
    if (field !== null && typeof field !== 'string' && typeof field !== 'boolean' &&
        !(typeof field === 'number' && Number.isFinite(field))) throw new Error('Invalid paper record');
  }
  return v as PaperRecord;
}
function parseOrder(value: unknown): PaperRecord {
  const v = parsePaperRecord(value);
  for (const key of ['order_id', 'execution_id', 'venue', 'symbol']) string(v[key]);
  if (!['buy', 'sell'].includes(string(v.side)) || v.status !== 'filled' || number(v.quantity) <= 0 || number(v.timestamp_ms) < 0) throw new Error('Invalid paper order');
  return v;
}
function parseFill(value: unknown): PaperRecord {
  const v = parsePaperRecord(value);
  for (const key of ['fill_id', 'order_id', 'execution_id', 'venue', 'symbol', 'reason_code', 'reason_text']) string(v[key]);
  if (!['buy', 'sell'].includes(string(v.side)) || number(v.timestamp_ms) < 0 || number(v.fee_usd) < 0) throw new Error('Invalid paper fill');
  for (const key of ['quantity', 'price', 'notional_usd']) if (number(v[key]) <= 0) throw new Error('Invalid fill quantity or price');
  return v;
}
function parseReconciliation(value: unknown): PaperRecord {
  const v = parsePaperRecord(value);
  number(v.expected_net_pct);
  if (v.simulated_net_pct !== null) number(v.simulated_net_pct);
  for (const key of ['fees_pct', 'slippage_pct', 'slippage_cost_usd', 'depth_slippage_pct']) if (number(v[key]) < 0) throw new Error('Invalid reconciliation cost');
  if (v.data_age_ms !== null && number(v.data_age_ms) < 0) throw new Error('Invalid reconciliation age');
  string(v.decision_reason);
  if (!['filled', 'rejected'].includes(string(v.execution_status))) throw new Error('Invalid execution status');
  if (v.execution_status === 'filled' && (v.simulated_net_pct === null || v.data_age_ms === null)) throw new Error('Incomplete filled reconciliation');
  return v;
}
export const getPaperOrders = () => request('/paper/orders', [] as PaperRecord[], (v) => list(v, parseOrder));
export const getPaperFills = () => request('/paper/fills', [] as PaperRecord[], (v) => list(v, parseFill));
export const getReconciliation = () => request('/reconciliation', [] as PaperRecord[], (v) => list(v, (row) => {
  const parsed = parseReconciliation(row); string(parsed.execution_id); return parsed;
}));

export async function simulatePaperOrder(item: Opportunity): Promise<PaperReport> {
  if (isMockMode) throw new Error('Configure a backend to simulate. Demo fixtures cannot create fills.');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(`${baseUrl}/paper/orders/simulate`, {
      method: 'POST', credentials: 'omit', cache: 'no-store', signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: item.symbol, buy_venue: item.buy_venue, sell_venue: item.sell_venue, notional_usd: item.simulation_notional_usd }),
    });
    if (!response.ok) throw new Error('Simulation request failed');
    const v = record(await response.json());
    const orders = list(v.orders, parseOrder), fills = list(v.fills, parseFill);
    const reconciliation = parseReconciliation(v.reconciliation);
    if (!['filled', 'rejected'].includes(string(v.status)) || reconciliation.execution_status !== v.status ||
        (v.status === 'rejected' && (orders.length || fills.length)) ||
        (v.status === 'filled' && (orders.length !== 2 || fills.length !== 2)) ||
        [...orders, ...fills].some((row) => row.execution_id !== v.execution_id)) throw new Error('Inconsistent paper report');
    return { execution_id: string(v.execution_id), status: string(v.status),
      reason_code: string(v.reason_code), reason_text: string(v.reason_text),
      orders, fills, reconciliation };
  } catch {
    throw new Error('Simulation response unavailable or invalid. Outcome unknown; refresh the paper ledger before retrying.');
  } finally { clearTimeout(timeout); }
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
      who: v.who === undefined ? undefined : string(v.who),
      opportunity_id: v.opportunity_id === undefined ? undefined : string(v.opportunity_id),
      decision: v.decision === undefined ? undefined : string(v.decision),
      execution_id: v.execution_id === undefined ? undefined : string(v.execution_id),
    };
  }));
}
