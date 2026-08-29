import { marketScope, platformMode, safetyState } from './mockData';

export interface DashboardSettings {
  trading_mode: string;
  market_scope: string;
  live_trading_enabled: boolean;
  t_invest_sandbox: boolean;
}

const mockSettings: DashboardSettings = {
  trading_mode: platformMode,
  market_scope: marketScope,
  live_trading_enabled: false,
  t_invest_sandbox: true,
};

export async function getSettings(): Promise<DashboardSettings> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL;
  if (!baseUrl) return mockSettings;
  const response = await fetch(`${baseUrl}/settings`);
  if (!response.ok) throw new Error('Settings API is unavailable');
  return response.json() as Promise<DashboardSettings>;
}
