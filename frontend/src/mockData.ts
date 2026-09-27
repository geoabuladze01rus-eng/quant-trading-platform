import type { AuditEvent, DashboardSettings, OpportunityResponse, Risk, Venue } from './types';

// Demonstration fixtures only. Never substituted for a failed backend request.
export const mockSettings: DashboardSettings = {
  max_market_data_age_ms: 1000,
  trading_mode: 'paper', market_scope: 'mixed', live_trading_enabled: false,
  t_invest_sandbox: true, max_daily_loss_pct: 2,
  max_trade_notional_usd: 100, min_expected_net_pct: 0.1,
};
export const mockVenues: Venue[] = [
  ...['binance', 'bybit', 'okx'].map((name): Venue => ({
    depth_status: 'unavailable', bid_levels: 0, ask_levels: 0,
    name, market: 'crypto', mode: 'public_read_only', status: 'no_data', live_execution: false,
    symbol: 'BTC/USDT', data_age_ms: null, error: null, bid: null, ask: null, timestamp_source: null,
  })),
  { name: 't_invest', market: 'russian_stocks', mode: 'sandbox', status: 'no_data', live_execution: false,
    depth_status: 'unavailable', bid_levels: 0, ask_levels: 0,
    symbol: 'SBER', data_age_ms: null, error: null, bid: null, ask: null, timestamp_source: null },
];
export const mockOpportunities: OpportunityResponse = {
  status: 'ok',
  opportunities: [{
    id: 'demo-rejected', summary: 'Демо-расчёт: комиссии и проскальзывание полностью съедают разницу цен.',
    reason_code: 'insufficient_net_edge',
    reason_text: 'Итоговая разница после расходов ниже минимального порога.',
    risk_score: 'blocked', simulation_notional_usd: '10',
    strategy: 'cross_venue_spread', symbol: 'BTC/USDT',
    buy_venue: 'binance', sell_venue: 'bybit', gross_spread_pct: 0.25,
    fees_pct: 0.2, slippage_pct: 0.05, expected_net_pct: 0,
    max_notional_usd: 100, approved: false,
    reason: 'После комиссий и проскальзывания расчётная разница ниже минимального порога 0,10%.',
    data_age_ms: 100,
  }],
};
export const mockRisk: Risk = {
  max_daily_loss_pct: 2, max_trade_notional_usd: 100, min_expected_net_pct: 0.1,
  live_trading_locked: true, stale_data_protection: true,
  api_error_protection: true, balance_mismatch_protection: true,
};
export const mockAudit: AuditEvent[] = [{
  id: 'demo-rejected', timestamp: 'Демо', market_scope: 'crypto',
  event: 'rejected', strategy: 'cross_venue_spread', reason: mockOpportunities.opportunities[0].reason,
}];
