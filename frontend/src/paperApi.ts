import type { Opportunity } from './types';

const base = import.meta.env.VITE_API_BASE_URL?.trim().replace(/\/+$/, '');
export const paperConfigured = !!base;
export type Json = null | boolean | string | number | Json[] | { [key: string]: Json };
export type Row = { [key: string]: Json };
export type OrderStatus = 'preview' | 'created' | 'accepted' | 'partially_filled' | 'filled' | 'cancelled' | 'rejected' | 'failed';
export type PaperOrder = Row & { id: string; symbol: string; status: OrderStatus; reason_code: string; human_reason: string; notional_usdt: string };
export type Account = Row & { id: string; virtual_equity_usdt: string | null; realized_pnl_usdt: string; unrealized_pnl_usdt: string | null; fees_paid_usdt: string; slippage_cost_usdt: string };
export type Balance = Row & { asset: string; available: string; reserved: string; total: string };
export type Reconciliation = Row & { status: 'ok' | 'error'; issues: Row[] };
export interface CommandResult { order: PaperOrder; fills: Row[]; account: Account; balances: Balance[]; positions: Row[]; reconciliation: Reconciliation; explanation: Row; paper_only: true }
export interface Portfolio { account: Account; balances: Balance[]; orders: PaperOrder[]; fills: Row[]; positions: Row[]; performance: Row; reconciliation: Reconciliation }
export interface ResidualDecision extends Row { status: 'flat' | 'hedge_required' | 'halted'; reason_code: string; human_reason: string; residual_quantity: string; residual_notional_usd: string | null; mark_source: string | null; mark_age_ms: number | null; configured_cap_usd: string; filled_leg: string; hedge_side: string | null; reason_not_executed: string; hedge_proposal: Row | null; paper_only: true; live_execution: false }
export interface ResidualObservation extends Row { execution_group_id: string; symbol: string; decision: ResidualDecision; paper_only: true; live_execution: false }

function object(value: unknown): Row {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected object');
  return value as Row;
}
function text(value: unknown): string { if (typeof value !== 'string') throw new Error('Expected text'); return value; }
function decimal(value: unknown): string {
  const result = text(value);
  if (!/^-?\d+(?:\.\d+)?$/.test(result) || !Number.isFinite(Number(result))) throw new Error('Expected exact decimal string');
  return result;
}
function nullableDecimal(value: unknown): string | null {
  return value === null ? null : decimal(value);
}
function rows<T>(value: unknown, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new Error('Expected array'); return value.map(parse);
}
function required(v: Row, strings: string[], decimals: string[] = []): void {
  strings.forEach((key) => text(v[key])); decimals.forEach((key) => decimal(v[key]));
}
function account(value: unknown): Account {
  const v = object(value);
  required(v, ['id', 'status', 'created_at', 'updated_at'], ['realized_pnl_usdt', 'fees_paid_usdt', 'slippage_cost_usdt']);
  nullableDecimal(v.virtual_equity_usdt); nullableDecimal(v.unrealized_pnl_usdt);
  return v as Account;
}
function balance(value: unknown): Balance {
  const v = object(value); required(v, ['account_id', 'asset', 'updated_at'], ['available', 'reserved', 'total']);
  if (['available', 'reserved', 'total'].some((key) => Number(v[key]) < 0)) throw new Error('Negative balance');
  return v as Balance;
}
function order(value: unknown, preview = false): PaperOrder {
  const v = object(value);
  if (preview && v.id === undefined) v.id = '';
  required(v, ['id', 'symbol', 'status', 'reason_code', 'human_reason', 'buy_venue', 'sell_venue'], ['notional_usdt']);
  if (!['preview', 'created', 'accepted', 'partially_filled', 'filled', 'cancelled', 'rejected', 'failed'].includes(text(v.status)) || v.paper_only !== true || (!preview && v.status === 'preview')) throw new Error('Unsafe order');
  for (const key of ['requested_qty', 'filled_qty', 'remaining_qty', 'fee_usdt', 'slippage_cost_usdt']) if (v[key] !== undefined) decimal(v[key]);
  return v as PaperOrder;
}
function fill(value: unknown): Row {
  const v = object(value); required(v, ['id', 'order_id', 'venue', 'side'], ['qty', 'price', 'notional_usdt', 'fee_usdt']);
  if (!['buy', 'sell'].includes(text(v.side)) || Number(v.qty) <= 0 || Number(v.price) <= 0) throw new Error('Invalid fill');
  return v;
}
function position(value: unknown): Row {
  const v = object(value); required(v, ['id', 'symbol', 'status'], ['quantity', 'realized_pnl_usdt']);
  for (const key of ['average_price', 'market_value_usdt', 'unrealized_pnl_usdt', 'current_price']) nullableDecimal(v[key]);
  decimal(v.quantity); decimal(v.realized_pnl_usdt); return v;
}
function reconciliation(value: unknown): Reconciliation {
  const v = object(value);
  if (v.status !== 'ok' && v.status !== 'error') throw new Error('Invalid reconciliation');
  text(v.checked_at);
  const issues = rows(v.issues, (item) => { const row = object(item); required(row, ['reason_code', 'human_reason', 'severity']); return row; });
  if (v.status === 'ok' && issues.length) throw new Error('Inconsistent reconciliation');
  return { ...v, status: v.status, issues };
}
function performance(value: unknown): Row {
  const v = object(value);
  required(v, [], ['realized_pnl_usdt', 'fees_paid_usdt', 'slippage_cost_usdt']);
  nullableDecimal(v.unrealized_pnl_usdt);
  if (v.daily_pnl_usdt !== undefined) decimal(v.daily_pnl_usdt);
  return v;
}
function residualDecision(value: unknown): ResidualDecision {
  const v = object(value);
  required(v, ['status', 'reason_code', 'human_reason', 'filled_leg', 'reason_not_executed'], ['residual_quantity', 'configured_cap_usd']);
  nullableDecimal(v.residual_notional_usd);
  if (!['flat', 'hedge_required', 'halted'].includes(text(v.status)) || v.paper_only !== true || v.live_execution !== false) throw new Error('Unsafe residual decision');
  if (v.mark_source !== null) text(v.mark_source);
  if (v.mark_age_ms !== null && (typeof v.mark_age_ms !== 'number' || !Number.isInteger(v.mark_age_ms))) throw new Error('Invalid mark freshness');
  if (v.status !== 'hedge_required' && v.hedge_proposal !== null) throw new Error('Halted/flat state cannot propose a hedge');
  if (v.hedge_proposal !== null) { const proposal = object(v.hedge_proposal); if (proposal.confirmable !== false || proposal.execution_enabled !== false) throw new Error('Executable hedge controls are forbidden'); }
  return v as ResidualDecision;
}
function command(value: unknown, preview = false): CommandResult {
  const v = object(value);
  if (v.paper_only !== true) throw new Error('Paper-only state unverified');
  const result = { order: order(v.order, preview), fills: rows(v.fills, fill), account: account(v.account), balances: rows(v.balances, balance), positions: rows(v.positions, position), reconciliation: reconciliation(v.reconciliation), explanation: object(v.explanation), paper_only: true as const };
  if (result.order.status === 'rejected' && result.fills.length) throw new Error('Rejected order has fills');
  return result;
}
async function request<T>(path: string, parse: (value: unknown) => T, payload?: object, key?: string): Promise<T> {
  if (!base) throw new Error('Backend not configured. Demo mode cannot create paper records.');
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(`${base}${path}`, { method: payload ? 'POST' : 'GET', credentials: 'omit', cache: 'no-store', signal: controller.signal,
      headers: { ...(payload ? { 'Content-Type': 'application/json' } : {}), ...(key ? { 'Idempotency-Key': key } : {}) },
      body: payload ? JSON.stringify(payload) : undefined });
    if (!response.ok) throw new Error('Backend request rejected');
    return parse(await response.json());
  } catch {
    throw new Error(payload && key ? 'Outcome unknown. Do not submit again. Refresh the ledger and inspect the request key.' : 'Backend unavailable or invalid data. Paper actions are disabled.');
  } finally { clearTimeout(timer); }
}
const payload = (item: Opportunity) => ({ symbol: item.symbol, buy_venue: item.buy_venue, sell_venue: item.sell_venue, notional_usdt: decimal(item.simulation_notional_usd) });
export const previewOrder = (item: Opportunity) => request('/paper/orders/preview', (v) => command(v, true), payload(item));
export const createOrder = (item: Opportunity, key: string) => request('/paper/orders', command, payload(item), key);
export const cancelOrder = (id: string, key: string) => request(`/paper/orders/${encodeURIComponent(id)}/cancel`, command, {}, key);
export const getOrder = (id: string) => request(`/paper/orders/${encodeURIComponent(id)}`, order);
export async function getPortfolio(): Promise<Portfolio> {
  const [a, b, o, f, p, perf, rec] = await Promise.all([
    request('/paper/account', account), request('/paper/balances', (v) => rows(v, balance)),
    request('/paper/orders', (v) => rows(v, order)), request('/paper/fills', (v) => rows(v, fill)),
    request('/paper/positions', (v) => rows(v, position)), request('/paper/performance', performance), request('/paper/reconciliation', reconciliation),
  ]);
  return { account: a, balances: b, orders: o, fills: f, positions: p, performance: perf, reconciliation: rec };
}
export function getResidualExposure(): Promise<ResidualObservation[]> {
  return request('/paper/residual-exposure', (value) => {
    const v = object(value);
    if (v.paper_only !== true || v.live_execution !== false) throw new Error('Unsafe residual view');
    return rows(v.items, (item) => {
      const row = object(item);
      required(row, ['execution_group_id', 'symbol']);
      if (row.paper_only !== true || row.live_execution !== false) throw new Error('Unsafe observation');
      return { ...row, decision: residualDecision(row.decision) } as ResidualObservation;
    });
  });
}
export function getAuditPage(offset: number, filters: { event_type?: string; order_id?: string; reason_code?: string; correlation_id?: string } = {}) {
  const params = new URLSearchParams({ limit: '50', offset: String(offset), ...filters });
  return request(`/audit?${params}`, (value) => {
    const v = object(value);
    for (const key of ['total', 'limit', 'offset']) if (typeof v[key] !== 'number' || !Number.isInteger(v[key]) || Number(v[key]) < 0) throw new Error('Invalid pagination');
    return { items: rows(v.items, (item) => { const row = object(item); required(row, ['event_id', 'timestamp', 'actor', 'event_type', 'reason_code', 'human_reason']); return row; }), total: Number(v.total), limit: Number(v.limit), offset: Number(v.offset) };
  });
}
