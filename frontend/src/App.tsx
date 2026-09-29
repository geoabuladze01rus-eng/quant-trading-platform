import {
  Activity, Bot, CircleDollarSign, Database, LockKeyhole, RefreshCw,
  ShieldCheck, Signal, WalletCards,
} from 'lucide-react';
import { useEffect, useState } from 'react';

import { getAudit, getOpportunities, getRisk, getRobotStatus, getSettings, getVenues, isMockMode } from './apiClient';
import { PaperAlpha } from './PaperAlpha';
import type { AuditEvent, DashboardSettings, Opportunity, OpportunityResponse, Risk, RobotStatus, Venue } from './types';

const PAIRS = ['BTC/USDT', 'ETH/USDT', 'LTC/USDT'] as const;
const CRYPTO_VENUES = ['binance', 'bybit', 'okx'] as const;
const venueName = (value: string) => ({ binance: 'Binance', bybit: 'Bybit', okx: 'OKX' }[value] ?? value);
const pct = (value: number, digits = 2) => `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;

interface Slice<T> { data: T | null; error: string }
interface Dashboard {
  settings: Slice<DashboardSettings>;
  venues: Slice<Venue[]>;
  opportunities: Slice<OpportunityResponse>;
  risk: Slice<Risk>;
  audit: Slice<AuditEvent[]>;
  robot: Slice<RobotStatus>;
}

const pending = <T,>(): Slice<T> => ({ data: null, error: '' });
const initialDashboard = (): Dashboard => ({
  settings: pending(), venues: pending(), opportunities: pending(), risk: pending(), audit: pending(), robot: pending(),
});
async function loadSlice<T>(promise: Promise<T>, label: string): Promise<Slice<T>> {
  try { return { data: await promise, error: '' }; }
  catch { return { data: null, error: `${label} недоступен` }; }
}

function robotView(robot: RobotStatus | null) {
  if (!robot) return { label: 'Не подтверждён', tone: 'neutral', text: 'Статус автоматического робота недоступен. Остальные safety gates проверяются отдельно.' };
  const state = robot.state;
  if (state === 'halted') return { label: 'Остановлен', tone: 'danger', text: robot.human_reason ?? robot.reason_code };
  if (state === 'needs_review') return { label: 'Нужна проверка', tone: 'warning', text: robot.human_reason ?? robot.reason_code };
  if (state === 'paused') return { label: 'На паузе', tone: 'warning', text: robot.human_reason ?? robot.reason_code };
  if (state === 'filled') return { label: 'Paper-сделка учтена', tone: 'positive', text: robot.human_reason ?? robot.reason_code };
  if (state === 'scanning') return { label: 'Сканирует рынок', tone: 'positive', text: robot.human_reason ?? 'Проверяет spread и directional-сигналы после fees и slippage.' };
  if (state === 'disabled') return { label: 'Выключен', tone: 'neutral', text: robot.human_reason ?? 'Автоматические paper-сделки отключены.' };
  return { label: state, tone: 'neutral', text: robot.human_reason ?? robot.reason_code };
}

function PairCard({ symbol, venues, opportunities, robot }: { symbol: string; venues: Venue[]; opportunities: Opportunity[]; robot: RobotStatus | null }) {
  const sources = CRYPTO_VENUES.map((name) => venues.find((venue) => venue.name === name && venue.symbol === symbol));
  const healthy = sources.filter((source) => source?.status === 'ok').length;
  const best = opportunities.filter((item) => item.symbol === symbol).sort((a, b) => b.expected_net_pct - a.expected_net_pct)[0];
  const robotState = robot?.symbol_states?.find((item) => item.symbol === symbol);
  const directionalSide = typeof robotState?.signal?.side === 'string' ? robotState.signal.side : null;
  const directionalEdge = typeof robotState?.signal?.net_edge_pct === 'string' ? Number(robotState.signal.net_edge_pct) : null;
  return <article className="pair-card">
    <div className="pair-card-head">
      <div><span className="pair-code">{symbol}</span><small>Spot · USDT</small></div>
      <span className={`state-chip ${healthy === 3 ? 'positive' : healthy ? 'warning' : 'neutral'}`}>{healthy}/3 источника</span>
    </div>
    <div className="venue-quotes">
      {CRYPTO_VENUES.map((name, index) => {
        const source = sources[index];
        const usable = source?.status === 'ok';
        return <div className="venue-quote" key={`${name}:${symbol}`}>
          <div><span className={`health-dot ${usable ? 'ok' : source?.status ?? 'missing'}`} /> <strong>{venueName(name)}</strong></div>
          <span>{usable ? `${source?.bid} / ${source?.ask}` : source?.status === 'stale' ? 'устарело' : 'нет данных'}</span>
        </div>;
      })}
    </div>
    <div className="pair-signal">
      <span>{directionalSide ? `${venueName(robot?.primary_venue ?? '')} · ${directionalSide.toUpperCase()}` : best ? `${venueName(best.buy_venue)} → ${venueName(best.sell_venue)}` : 'Сигнал пока не сформирован'}</span>
      <strong className={directionalSide ? 'value-positive' : best?.approved ? 'value-positive' : best ? 'value-danger' : 'muted'}>{directionalEdge !== null ? pct(directionalEdge, 4) : best ? pct(best.expected_net_pct) : '—'}</strong>
    </div>
    {robotState && <p className="reason-line"><span>{robotState.state}</span>{robotState.human_reason}</p>}
  </article>;
}

function SignalCard({ item }: { item: Opportunity }) {
  return <article className="signal-card">
    <div className="signal-main">
      <div className="signal-symbol"><strong>{item.symbol}</strong><span>{venueName(item.buy_venue)} → {venueName(item.sell_venue)}</span></div>
      <div className="signal-number"><small>Net edge</small><strong className={item.approved ? 'value-positive' : 'value-danger'}>{pct(item.expected_net_pct, 4)}</strong></div>
      <div className="signal-number"><small>Fees</small><strong>{item.fees_pct.toFixed(2)}%</strong></div>
      <span className={`state-chip ${item.approved ? 'positive' : 'danger'}`}>{item.approved ? 'Paper-ready' : 'Отклонён'}</span>
    </div>
    <p className="reason-line"><span>{item.approved ? 'Почему подходит' : 'Причина отказа'}</span>{item.reason_text || item.reason}</p>
    <details>
      <summary>Все метрики и risk decision</summary>
      <div className="detail-metrics">
        <div><small>Gross edge</small><strong>{pct(item.gross_spread_pct, 4)}</strong></div>
        <div><small>Fees</small><strong>−{item.fees_pct.toFixed(4)}%</strong></div>
        <div><small>Slippage</small><strong>−{item.slippage_pct.toFixed(4)}%</strong></div>
        <div><small>Net edge</small><strong>{pct(item.expected_net_pct, 4)}</strong></div>
        <div><small>Возраст данных</small><strong>{item.data_age_ms} ms</strong></div>
        <div><small>Reason code</small><strong>{item.reason_code}</strong></div>
      </div>
    </details>
  </article>;
}

export function App() {
  const [dashboard, setDashboard] = useState<Dashboard>(initialDashboard);
  const [updatedAt, setUpdatedAt] = useState('');
  const [receivedAt, setReceivedAt] = useState(0);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      const requestedAt = Date.now();
      const [settings, venues, opportunities, risk, audit, robot] = await Promise.all([
        loadSlice(getSettings(), 'Настройки'), loadSlice(getVenues(), 'Котировки'),
        loadSlice(getOpportunities(), 'Сигналы'), loadSlice(getRisk(), 'Risk state'),
        loadSlice(getAudit(), 'Audit'), loadSlice(getRobotStatus(), 'Робот'),
      ]);
      if (disposed) return;
      setDashboard({ settings, venues, opportunities, risk, audit, robot });
      setReceivedAt(requestedAt);
      setUpdatedAt(new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
      if (!isMockMode) timer = setTimeout(() => void load(), 10000);
    }
    void load();
    return () => { disposed = true; clearTimeout(timer); };
  }, [refresh]);

  const settings = dashboard.settings.data;
  const venues = dashboard.venues.data ?? [];
  const opportunities = dashboard.opportunities.data?.opportunities ?? [];
  const risk = dashboard.risk.data;
  const audit = dashboard.audit.data ?? [];
  const robot = dashboard.robot.data;
  const unsafe = (!!settings && (settings.live_trading_enabled || settings.trading_mode === 'live')) ||
    (!!risk && !risk.live_trading_locked) || venues.some((venue) => venue.live_execution);
  const safetyVerified = !!settings && !!risk && !!dashboard.venues.data && !!dashboard.opportunities.data && !unsafe;
  const failures = Object.values(dashboard).map((slice) => slice.error).filter(Boolean);
  const healthyBooks = venues.filter((venue) => venue.market === 'crypto' && venue.status === 'ok').length;
  const robotState = robotView(robot);

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#overview" aria-label="Paper Robot — обзор">
        <div className="brand-mark"><Bot size={21} /></div>
        <div><strong>Paper Robot</strong><span>Crypto · risk first</span></div>
      </a>
      <nav className="nav-list" aria-label="Разделы">
        <a className="nav-item active" href="#overview"><Activity size={18} /> Обзор</a>
        <a className="nav-item" href="#markets"><Database size={18} /> Рынки</a>
        <a className="nav-item" href="#signals"><Signal size={18} /> Сигналы</a>
        <a className="nav-item" href="#paper"><WalletCards size={18} /> Виртуальные деньги</a>
        <a className="nav-item" href="#safety"><ShieldCheck size={18} /> Безопасность</a>
      </nav>
      <div className="sidebar-safety"><LockKeyhole size={18} /><div><strong>Только PAPER</strong><span>Live controls отсутствуют</span></div></div>
    </aside>

    <main className="main-content" id="overview">
      <header className="topbar">
        <div><p className="eyebrow">Виртуальный крипто-портфель</p><h1>Робот торгует только на бумаге</h1><p className="lede">Рыночные данные реальные. Деньги, сделки и результат — виртуальные.</p></div>
        <div className="header-actions">
          <span className="state-chip paper">PAPER MODE</span>
          <span className="state-chip locked"><LockKeyhole size={14} /> Реальные деньги выключены</span>
          <button className="icon-button" type="button" onClick={() => setRefresh((value) => value + 1)} aria-label="Обновить данные"><RefreshCw size={17} /></button>
        </div>
      </header>

      <section className={`system-notice ${unsafe ? 'danger' : failures.length ? 'warning' : 'positive'}`} role={unsafe || failures.length ? 'alert' : 'status'}>
        <div><strong>{unsafe ? 'Safety state не подтверждён — paper-действия заблокированы' : failures.length ? 'Часть данных временно недоступна' : isMockMode ? 'Демонстрационный режим' : 'Все критические paper-проверки пройдены'}</strong>
          <p>{unsafe ? 'Backend сообщил небезопасную конфигурацию.' : failures.length ? `${failures.join(' · ')}. Остальные read-only карточки продолжают работать.` : `Обновлено ${updatedAt || '—'} · автоматическое обновление каждые 10 секунд.`}</p></div>
        <span>{healthyBooks}/9 книг доступны</span>
      </section>

      <section className="overview-grid" aria-label="Главные состояния">
        <article className="hero-card robot-card">
          <div className="card-icon"><Bot size={20} /></div><small>Состояние робота</small>
          <strong>{robotState.label}</strong><p>{robotState.text}</p>
          <div className="card-footer"><span className={`health-dot ${robotState.tone}`} /> {robot ? `${venueName(robot.primary_venue)} · ${robot.paper_only ? 'paper only' : 'не подтверждено'}` : 'endpoint недоступен'}</div>
        </article>
        <article className="hero-card">
          <div className="card-icon"><Database size={20} /></div><small>Market data</small>
          <strong>{healthyBooks}/9 свежих книг</strong><p>Binance, Bybit и OKX по трём точным spot-парам.</p>
          <div className="card-footer">Лимит свежести: {settings ? `${settings.max_market_data_age_ms} ms` : '—'}</div>
        </article>
        <article className="hero-card">
          <div className="card-icon"><ShieldCheck size={20} /></div><small>Risk state</small>
          <strong>{safetyVerified ? 'Защита включена' : 'Не подтверждена'}</strong><p>Net edge минимум {risk ? `${risk.min_expected_net_pct.toFixed(2)}%` : '—'} после fees и slippage.</p>
          <div className="card-footer">Лимит paper-сделки: {risk ? `${risk.max_trade_notional_usd.toFixed(0)} USDT` : '—'}</div>
        </article>
      </section>

      <section id="markets">
        <div className="section-heading"><div><p className="eyebrow">Точный universe</p><h2>Spot-пары и venues</h2></div><span>Bid / Ask · read-only</span></div>
        <div className="pair-grid">{PAIRS.map((symbol) => <PairCard key={symbol} symbol={symbol} venues={venues} opportunities={opportunities} robot={robot} />)}</div>
      </section>

      <section id="signals">
        <div className="section-heading"><div><p className="eyebrow">После всех издержек</p><h2>Сигналы и причины решений</h2></div><span>{opportunities.length} решений</span></div>
        <div className="signal-list">
          {opportunities.length ? opportunities.map((item) => <SignalCard item={item} key={item.id} />) :
            <div className="empty-card"><Signal size={22} /><strong>Сигналов пока нет</strong><p>Робот ждёт свежие книги или достаточный net edge. Это не ошибка и не повод ослаблять risk limits.</p></div>}
        </div>
      </section>

      <PaperAlpha opportunities={opportunities} venues={venues} audit={audit} receivedAt={receivedAt} maxAge={settings?.max_market_data_age_ms ?? 0} actionsEnabled={safetyVerified} />

      <section className="safety-grid" id="safety">
        <article className="panel">
          <div className="section-heading"><div><p className="eyebrow">Fail closed</p><h2>Активные защиты</h2></div><ShieldCheck size={20} /></div>
          <ul className="protection-list">
            <li><span className={risk?.stale_data_protection ? 'check on' : 'check'}>✓</span> Stale market data блокирует preview</li>
            <li><span className={risk?.balance_mismatch_protection ? 'check on' : 'check'}>✓</span> Balance mismatch останавливает account</li>
            <li><span className={risk?.api_error_protection ? 'check on' : 'check'}>✓</span> Ошибка API не заменяется mock-данными</li>
            <li><span className={risk?.live_trading_locked ? 'check on' : 'check'}>✓</span> Live execution отсутствует</li>
          </ul>
        </article>
        <article className="panel audit-card">
          <div className="section-heading"><div><p className="eyebrow">Последние решения</p><h2>Audit trail</h2></div><Activity size={20} /></div>
          {audit.length ? audit.slice(0, 5).map((event) => <div className="audit-row" key={event.id}><div><strong>{event.event}</strong><span>{event.strategy}</span></div><p>{event.reason}</p></div>) : <p className="empty-copy">Audit events недоступны или ещё не созданы.</p>}
        </article>
      </section>
      <footer><CircleDollarSign size={16} /> Все значения относятся к виртуальному paper account. Доходность не обещается.</footer>
    </main>
  </div>;
}
