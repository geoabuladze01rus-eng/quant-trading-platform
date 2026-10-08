export const now = 1791450000000;
export const candidate = {
  signal_id: 'a'.repeat(64), asset: 'BTC/USDT', setup: 'Momentum', score: '85',
  domains: { A: '25', B: '20', C: '20', D: '20', E: '0' }, confidence: 'VERY HIGH',
  rejected_reason: null, market_regime: 'trend', timestamp_ms: now,
  paper_only: true, live_execution: false, missed_reason: null,
};
export function fixture() {
  return {
    generated_at_ms: now, paper_only: true, live_execution: false,
    assets: { 'BTC/USDT': { status: 'ok', timestamp_ms: now, valid_until_ms: now + 1000,
      warnings: [], candidates: [structuredClone(candidate)] } },
    journal: { kind: 'historical_candidates', limit: 100, rows: [structuredClone(candidate)] },
    providers: [{ source: 'Gina', symbol: 'BTC/USDT', status: 'stale' }],
    outcomes: { kind: 'estimated_forward_markout', calibration: {} },
    observed_outcomes: { kind: 'observed_net_return', calibration: {} },
  };
}
