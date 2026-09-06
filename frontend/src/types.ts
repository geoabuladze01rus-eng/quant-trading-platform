export interface DashboardSettings {
  max_market_data_age_ms: number;
  trading_mode: string;
  market_scope: string;
  live_trading_enabled: boolean;
  t_invest_sandbox: boolean;
  max_daily_loss_pct: number;
  max_trade_notional_usd: number;
  min_expected_net_pct: number;
}

export interface Venue {
  depth_status: string;
  bid_levels: number;
  ask_levels: number;
  name: string;
  market: string;
  mode: 'public_read_only' | 'sandbox' | 'disabled';
  status: 'ok' | 'no_data' | 'stale' | 'error' | 'disabled';
  symbol: string;
  data_age_ms: number | null;
  error: string | null;
  bid: string | null;
  ask: string | null;
  timestamp_source: string | null;
  live_execution: boolean;
}

export interface Opportunity {
  id: string;
  summary: string;
  reason_code: string;
  reason_text: string;
  risk_score: 'passed' | 'blocked';
  simulation_notional_usd: string;
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

export type PaperRecord = Record<string, string | number | boolean | null>;
export interface PaperReport {
  execution_id: string;
  status: string;
  reason_code: string;
  reason_text: string;
  orders: PaperRecord[];
  fills: PaperRecord[];
  reconciliation: PaperRecord;
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
  opportunity_id?: string;
  who?: string;
  decision?: string;
  execution_id?: string;
  id: string;
  timestamp: string;
  market_scope: string;
  event: string;
  strategy: string;
  reason: string;
}
