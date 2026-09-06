import type { AuditEvent, DashboardSettings, OpportunityResponse, Risk, Venue } from './types';

// Demonstration fixtures only. Never substituted for a failed backend request.
export const mockSettings: DashboardSettings = {
  trading_mode: 'paper', market_scope: 'mixed', live_trading_enabled: false,
  t_invest_sandbox: true, max_daily_loss_pct: 2,
  max_trade_notional_usd: 100, min_expected_net_pct: 0.1,
};
export const mockVenues: Venue[] = [
  ...['binance', 'bybit', 'okx'].map((name) => ({
    name, market: 'crypto', status: 'mock', live_execution: false,
  })),
  { name: 't_invest', market: 'russian_stocks', status: 'sandbox', live_execution: false },
];
export const mockOpportunities: OpportunityResponse = {
  status: 'ok',
  opportunities: [{
    strategy: 'cross_venue_spread', symbol: 'BTC/USDT',
    buy_venue: 'binance', sell_venue: 'bybit', gross_spread_pct: 0.25,
    fees_pct: 0.2, slippage_pct: 0.05, expected_net_pct: 0,
    max_notional_usd: 100, approved: false,
    reason: 'Demo: net edge after fees and slippage is below the minimum 0.10%.',
    data_age_ms: 100,
  }],
};
export const mockRisk: Risk = {
  max_daily_loss_pct: 2, max_trade_notional_usd: 100, min_expected_net_pct: 0.1,
  live_trading_locked: true, stale_data_protection: true,
  api_error_protection: true, balance_mismatch_protection: true,
};
export const mockAudit: AuditEvent[] = [{
  id: 'demo-rejected', timestamp: 'Demo', market_scope: 'crypto',
  event: 'rejected', strategy: 'cross_venue_spread', reason: mockOpportunities.opportunities[0].reason,
}];
