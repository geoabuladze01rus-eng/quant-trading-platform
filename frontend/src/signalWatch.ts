/** Read-only v4 contract. Decimal values remain strings throughout the UI. */
export const symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'] as const;
const setups = ['Trend Pullback', 'Breakout + Retest', 'Liquidity Sweep', 'Momentum'];
const providers = ['TraderSpy', 'CryptoAudit', 'TradingCursor', 'Gina', 'Exa', 'Blockscout'];
export interface WatchCandidate {
  signal_id: string; asset: string; setup: string; score: string;
  domains: Record<string, string>; confidence: string; rejected_reason: string | null;
  market_regime: string; timestamp_ms: number; missed_reason: string | null;
}
export interface WatchAsset {
  status: string; timestamp_ms: number; valid_until_ms: number;
  candidates: WatchCandidate[]; warnings: string[];
}
export interface Calibration {
  samples: number; positive: number; mean_net_return: string | null;
}
export interface WatchProvider {
  source: string; symbol: string; status: string; valid_until_ms: number | null;
}
export interface WatchSnapshot {
  status?: string; generated_at_ms: number; received_age_ms: number;
  paper_only: true; live_execution: false;
  assets: Record<string, WatchAsset>;
  journal: { kind: 'historical_candidates'; rows: WatchCandidate[] };
  providers: WatchProvider[];
  outcomes: { kind: 'estimated_forward_markout'; calibration: Record<string, Calibration> };
  observed_outcomes: { kind: 'observed_net_return'; calibration: Record<string, Calibration> };
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid watch object');
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== 'string' || value.length > 250) throw new Error('Invalid watch text');
  return value;
}
function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('Invalid watch clock/count');
  return value;
}
function decimal(value: unknown): string {
  const result = text(value);
  if (!/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:E[+-]?\d+)?$/.test(result)) throw new Error('Invalid Decimal text');
  return result;
}
function array(value: unknown, limit: number): unknown[] {
  if (!Array.isArray(value) || value.length > limit) throw new Error('Invalid bounded watch list');
  return value;
}
function safe(value: Record<string, unknown>): void {
  if (value.paper_only !== true || value.live_execution !== false) throw new Error('Unsafe watch execution');
}
function candidate(value: unknown, asset?: string): WatchCandidate {
  const row = object(value); safe(row);
  const id = text(row.signal_id), symbol = text(row.asset), setup = text(row.setup);
  const confidence = text(row.confidence);
  const reason = row.rejected_reason === null ? null : text(row.rejected_reason);
  if (!/^[a-f0-9]{64}$/.test(id) || !symbols.includes(symbol as typeof symbols[number]) ||
      (asset !== undefined && asset !== symbol) || !setups.includes(setup) ||
      !['HIGH', 'VERY HIGH', 'REJECTED'].includes(confidence) ||
      (confidence === 'REJECTED' ? !reason : reason !== null)) throw new Error('Unverified watch candidate');
  const domains = object(row.domains);
  if (Object.keys(domains).sort().join('') !== 'ABCDE') throw new Error('Missing confidence domains');
  return { signal_id: id, asset: symbol, setup, confidence, rejected_reason: reason,
    score: decimal(row.score), domains: Object.fromEntries(Object.entries(domains).map(([key, val]) => [key, decimal(val)])),
    market_regime: text(row.market_regime), timestamp_ms: integer(row.timestamp_ms),
    missed_reason: row.missed_reason == null ? null : text(row.missed_reason) };
}
function calibration(value: unknown): Record<string, Calibration> {
  return Object.fromEntries(Object.entries(object(value)).map(([key, item]) => {
    if (!['HIGH', 'VERY HIGH', 'REJECTED'].includes(key)) throw new Error('Invalid calibration bucket');
    const row = object(item), samples = integer(row.samples), positive = integer(row.positive);
    if (positive > samples || (samples === 0) !== (row.mean_net_return === null)) throw new Error('Invalid calibration sample');
    return [key, { samples, positive, mean_net_return: row.mean_net_return === null ? null : decimal(row.mean_net_return) }];
  }));
}
function empty(status: string): WatchSnapshot {
  return { status, generated_at_ms: 0, received_age_ms: 0, paper_only: true, live_execution: false, assets: {},
    journal: { kind: 'historical_candidates', rows: [] }, providers: [],
    outcomes: { kind: 'estimated_forward_markout', calibration: {} },
    observed_outcomes: { kind: 'observed_net_return', calibration: {} } };
}
export function parseSignalWatch(value: unknown, receivedAtMs: number = Date.now()): WatchSnapshot {
  const row = object(value); safe(row);
  const rawAssets = object(row.assets);
  if (row.status === 'disabled' && Object.keys(rawAssets).length === 0) return empty('disabled');
  const generated = integer(row.generated_at_ms);
  const receivedAge = integer(receivedAtMs) - generated;
  // Same fail-closed response budget as the evidence MCP bridge; no future-clock allowance.
  if (generated === 0 || receivedAge < 0 || receivedAge > 5000) throw new Error('Untrusted server clock');
  const assets = Object.fromEntries(Object.entries(rawAssets).map(([symbol, item]) => {
    if (!symbols.includes(symbol as typeof symbols[number])) throw new Error('Unsupported watch asset');
    const state = object(item), status = text(state.status), timestamp = integer(state.timestamp_ms);
    if (!['ok', 'stale', 'error'].includes(status)) throw new Error('Invalid watch status');
    const deadline = state.valid_until_ms === undefined ? 0 : integer(state.valid_until_ms);
    if (status === 'ok' && (deadline < generated || timestamp > generated || generated - timestamp > 60_000)) throw new Error('Expired watch data');
    const candidates = array(state.candidates, 4).map(item => candidate(item, symbol));
    if (status !== 'ok' && candidates.length) throw new Error('Untrusted watch candidates');
    return [symbol, { status, timestamp_ms: timestamp, valid_until_ms: deadline, candidates,
      warnings: state.warnings === undefined ? [] : array(state.warnings, 20).map(text) }];
  }));
  const journal = object(row.journal), outcomes = object(row.outcomes), observed = object(row.observed_outcomes);
  if (journal.kind !== 'historical_candidates' || journal.limit !== 100 ||
      outcomes.kind !== 'estimated_forward_markout' || observed.kind !== 'observed_net_return') throw new Error('Invalid research history');
  const sourceRows = array(row.providers, 18).map(item => {
    const source = object(item), name = text(source.source), symbol = text(source.symbol), status = text(source.status);
    if (!providers.includes(name) || !symbols.includes(symbol as typeof symbols[number]) ||
        !['ok', 'no_data', 'stale', 'error'].includes(status)) throw new Error('Invalid provider state');
    const deadline = source.valid_until_ms == null ? null : integer(source.valid_until_ms);
    if (status === 'ok' && (deadline === null || deadline < generated)) throw new Error('Unverified provider freshness');
    return { source: name, symbol, status, valid_until_ms: deadline };
  });
  return { generated_at_ms: generated, received_age_ms: receivedAge,
    paper_only: true, live_execution: false, assets,
    journal: { kind: 'historical_candidates', rows: array(journal.rows, 100).map(item => candidate(item)) },
    providers: sourceRows, outcomes: { kind: 'estimated_forward_markout', calibration: calibration(outcomes.calibration) },
    observed_outcomes: { kind: 'observed_net_return', calibration: calibration(observed.calibration) } };
}
export function visibleAssets(snapshot: WatchSnapshot, elapsedMs: number): Record<string, WatchAsset> {
  if (!Number.isSafeInteger(elapsedMs) || elapsedMs < 0) return {};
  return Object.fromEntries(Object.entries(snapshot.assets).map(([symbol, state]) => [symbol,
    state.status === 'ok' && snapshot.generated_at_ms + snapshot.received_age_ms + elapsedMs > state.valid_until_ms
      ? { ...state, status: 'stale', candidates: [] } : state]));
}
export function visibleProviders(snapshot: WatchSnapshot, elapsedMs: number): WatchProvider[] {
  return snapshot.providers.map(row => row.status === 'ok' && (
    !Number.isSafeInteger(elapsedMs) || elapsedMs < 0 || row.valid_until_ms === null ||
    snapshot.generated_at_ms + snapshot.received_age_ms + elapsedMs > row.valid_until_ms
  ) ? { ...row, status: 'stale' } : row);
}
export async function readSignalWatch(baseUrl: string | undefined, transport: typeof fetch = fetch,
  clock: () => number = () => Date.now()): Promise<WatchSnapshot> {
  if (!baseUrl?.trim()) return empty('not_connected');
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await transport(baseUrl.trim().replace(/\/+$/, '') + '/crypto-signal-watch', {
      method: 'GET', credentials: 'omit', cache: 'no-store', redirect: 'error', signal: controller.signal,
    });
    if (!response.ok) throw new Error('Unavailable watch response');
    if (!response.body) throw new Error('Missing watch body');
    const reader = response.body.getReader(), chunks: Uint8Array[] = [];
    let length = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.length;
        if (length > 1048576) throw new Error('Oversize watch response');
        chunks.push(value);
      }
    } catch (cause) { await reader.cancel(); throw cause; }
    finally { reader.releaseLock(); }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    return parseSignalWatch(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)), clock());
  } catch { throw new Error('Данные v4 недоступны или не прошли проверку. Кандидаты скрыты.'); }
  finally { clearTimeout(timeout); }
}
