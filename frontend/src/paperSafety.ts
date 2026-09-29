import { isMockMode } from './apiClient';
import type { Opportunity, Venue } from './types';

export function simulationBlock(item: Opportunity, venues: Venue[], elapsed: number, maxAge: number): string {
  if (isMockMode) return 'Backend не настроен: demo-данные не создают сделки.';
  if (!item.approved || item.risk_score !== 'passed') return item.reason_text;
  if (!Number.isFinite(maxAge) || maxAge <= 0 || elapsed < 0 || item.data_age_ms + elapsed > maxAge) return 'Котировка устарела. Обновите данные.';
  for (const name of [item.buy_venue, item.sell_venue]) {
    const venue = venues.find((candidate) => candidate.name === name && candidate.symbol === item.symbol);
    if (!venue || venue.status !== 'ok' || venue.live_execution || venue.mode !== 'public_read_only' || venue.depth_status !== 'available' ||
        venue.data_age_ms === null || venue.data_age_ms + elapsed > maxAge) return `${name}: нет свежей публичной глубины рынка.`;
  }
  return '';
}
