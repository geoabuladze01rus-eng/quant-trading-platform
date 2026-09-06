export interface DashboardSettings {
  trading_mode: string;
  market_scope: string;
  live_trading_enabled: boolean;
  t_invest_sandbox: boolean;
  max_daily_loss_pct: number;
  max_trade_notional_usd: number;
  min_expected_net_pct: number;
}

export interface Venue {
  name: string;
  market: string;
  status: string;
  live_execution: boolean;
}

export interface Opportunity {
  strategy: string;
  symbol: string;
  buy_venue: string;
  sell_venue: string;
  gross_spread_pct: number;
  fees_pct: number;
  slippage_pct: number;
  expected_net_pct: number;
  max_notional_usd: number;
  approved: boolean;
  reason: string;
  data_age_ms: number;
}

export interface OpportunityResponse {
  status: 'no_data' | 'ok';
  opportunities: Opportunity[];
}

export interface Risk {
  max_daily_loss_pct: number;
  max_trade_notional_usd: number;
  min_expected_net_pct: number;
  live_trading_locked: boolean;
  stale_data_protection: boolean;
  api_error_protection: boolean;
  balance_mismatch_protection: boolean;
}

export interface AuditEvent {
  id: string;
  timestamp: string;
  market_scope: string;
  event: string;
  strategy: string;
  reason: string;
}
