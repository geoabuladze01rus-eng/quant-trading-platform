import { Activity, AlertTriangle, BarChart3, DatabaseZap, FileText, Gauge, Lock, Settings, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';

import { getAudit, getOpportunities, getRisk, getSettings, getVenues, isMockMode } from './apiClient';
import type { AuditEvent, DashboardSettings, OpportunityResponse, Risk, Venue } from './types';

const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const pct = (value: number) => `${value.toFixed(2)}%`;
const market = (value: string) => ({ crypto: 'Crypto', russian_stocks: 'Russian market', mixed: 'Mixed' }[value] ?? value);
const venueName = (value: string) => ({ binance: 'Binance', bybit: 'Bybit', okx: 'OKX', t_invest: 'T-Invest' }[value] ?? value);

interface Dashboard {
  settings: DashboardSettings;
  venues: Venue[];
  opportunities: OpportunityResponse;
  risk: Risk;
  audit: AuditEvent[];
}

export function App() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState('');
  const [updatedAt, setUpdatedAt] = useState('');
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const [settings, venues, opportunities, risk, audit] = await Promise.all([
          getSettings(), getVenues(), getOpportunities(), getRisk(), getAudit(),
        ]);
        if (disposed) return;
        if (settings.live_trading_enabled || settings.trading_mode === 'live' ||
            !settings.t_invest_sandbox || !risk.live_trading_locked ||
            venues.some((venue) => venue.live_execution)) {
          throw new Error('Unsafe backend configuration. Paper/sandbox safety settings could not be confirmed.');
        }
        setData({ settings, venues, opportunities, risk, audit });
        setError('');
        setUpdatedAt(new Date().toLocaleTimeString());
      } catch (cause) {
        if (disposed) return;
        // Clear the previous snapshot: old approvals must not look current during an outage.
        setData(null);
        setError(cause instanceof Error ? cause.message : 'Backend unavailable. Data is not trusted.');
      } finally {
        if (!disposed && !isMockMode) timer = setTimeout(() => void load(), 10000);
      }
    }
    void load();
    return () => { disposed = true; clearTimeout(timer); };
  }, [refresh]);

  const rows = data?.opportunities.opportunities ?? [];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">QT</div>
          <div><strong>Quant Platform</strong><span>Mixed-market control</span></div>
        </div>
        <nav className="nav-list" aria-label="Main navigation">
          <a className="nav-item active" href="#command"><Gauge size={18} /> Command center</a>
          <a className="nav-item" href="#opportunities"><BarChart3 size={18} /> Opportunities</a>
          <a className="nav-item" href="#risk"><ShieldCheck size={18} /> Risk center</a>
          <a className="nav-item" href="#audit"><FileText size={18} /> Audit log</a>
          <a className="nav-item" href="#settings"><Settings size={18} /> Settings</a>
        </nav>
      </aside>
      <main className="main-content">
        <header className="topbar" id="command">
          <div><p className="eyebrow">Crypto arbitrage + T-Invest research</p><h1>Command center</h1></div>
          <div className="mode-cluster" aria-label="Platform state">
            <span className="status-pill paper">Mode: {data?.settings.trading_mode ?? 'unknown'}</span>
            <span className="status-pill paper">Scope: {data ? market(data.settings.market_scope) : 'unknown'}</span>
            <span className="status-pill locked"><Lock size={14} /> Live locked — read-only UI</span>
          </div>
        </header>

        <section className={`data-notice ${error ? 'data-error' : ''}`} role={error ? 'alert' : 'status'}>
          <div>
            <strong>{error ? 'Data unavailable — execution remains locked' : isMockMode ? 'Demo / mock data' : data ? 'Backend connected — read-only' : 'Connecting to backend…'}</strong>
            <p>{error || (isMockMode ? 'Illustrative fixtures only. Configure VITE_API_BASE_URL to read backend data.' : `Last successful refresh: ${updatedAt || 'pending'}. Refreshes every 10 seconds.`)}</p>
          </div>
          <button type="button" onClick={() => setRefresh((value) => value + 1)}>Refresh data</button>
        </section>

        <section className="metric-grid" aria-label="Safety metrics">
          <article className="metric-card warning"><span>Daily loss limit</span><strong>{data ? pct(data.risk.max_daily_loss_pct) : '—'}</strong><small>Portfolio PnL is not provided by this API</small></article>
          <article className="metric-card"><span>Minimum net edge</span><strong>{data ? pct(data.risk.min_expected_net_pct) : '—'}</strong><small>After fees and slippage</small></article>
          <article className="metric-card"><span>Per-trade notional limit</span><strong>{data ? money.format(data.risk.max_trade_notional_usd) : '—'}</strong><small>Paper simulation only</small></article>
          <article className="metric-card danger-soft"><span>Real execution</span><strong>Disabled in UI</strong><small>T-Invest: {data ? 'sandbox' : 'status unverified'}</small></article>
        </section>

        <section className="panel-grid">
          <article className="panel">
            <div className="panel-header"><div><p className="eyebrow">Reported venue state</p><h2>Market health</h2></div><DatabaseZap size={20} /></div>
            <div className="health-list">
              {data?.venues.map((venue) => <div className="health-row" key={venue.name}>
                <span>{venueName(venue.name)}</span><small>{market(venue.market)}</small><b className="warn">{venue.status}</b><small>Live locked</small>
              </div>)}
              {!data && <p className="empty-state">Venue health unavailable. Binance / Bybit / OKX / T-Invest are not verified.</p>}
            </div>
          </article>
          <article className="panel">
            <div className="panel-header"><div><p className="eyebrow">Decision stream</p><h2>Recent bot logic</h2></div><Activity size={20} /></div>
            <div className="decision-list">
              {data?.audit.slice(0, 4).map((event) => <div className="decision-row" key={event.id}>
                <strong>{event.event}</strong><small>{event.reason}</small>
              </div>)}
              {!data?.audit.length && <p className="empty-state">{data ? 'No audit events recorded.' : 'Audit data unavailable.'}</p>}
            </div>
          </article>
        </section>

        <section className="panel" id="opportunities">
          <div className="panel-header"><div><p className="eyebrow">After estimated fees and slippage</p><h2>Opportunities</h2></div><AlertTriangle size={20} /></div>
          <p className="settings-note">Spread compares the ask on the buy venue with the bid on the sell venue. Backend estimates deduct 0.20% fees and 0.05% slippage.</p>
          <p className="settings-note">Approved means eligible for paper simulation; it does not enable an order.</p>
          {!rows.length ? <p className="empty-state">{data ? 'No data: no eligible opportunities from current market quotes.' : 'Opportunities unavailable. Waiting for a trusted backend response.'}</p> :
            <div className="table-wrap"><table>
              <thead><tr><th>Strategy</th><th>Symbol</th><th>Buy / sell venues</th><th>Gross</th><th>Fees</th><th>Slippage</th><th>Net edge</th><th>Max notional</th><th>Data age</th><th>Risk decision</th><th>Reason</th></tr></thead>
              <tbody>{rows.map((item, index) => <tr key={`${item.strategy}-${item.symbol}-${item.buy_venue}-${item.sell_venue}-${index}`}>
                <td>{item.strategy}</td><td>{item.symbol}</td><td>{venueName(item.buy_venue)} / {venueName(item.sell_venue)}</td>
                <td>{pct(item.gross_spread_pct)}</td><td>{pct(item.fees_pct)}</td><td>{pct(item.slippage_pct)}</td>
                <td className={item.approved ? 'good' : 'muted'}>{pct(item.expected_net_pct)}</td><td>{money.format(item.max_notional_usd)}</td><td>{item.data_age_ms} ms at refresh</td>
                <td><span className={`decision ${item.approved ? 'approved' : 'rejected'}`}>{item.approved ? 'Paper approved' : 'Rejected'}</span></td><td className="reason-cell">{item.reason}</td>
              </tr>)}</tbody>
            </table></div>}
        </section>

        <section className="panel-grid" id="risk">
          <article className="panel">
            <div className="panel-header"><div><p className="eyebrow">Protections</p><h2>Risk center</h2></div><ShieldCheck size={20} /></div>
            {data ? <ul className="check-list">
              <li>Daily loss limit: {pct(data.risk.max_daily_loss_pct)}</li>
              <li>Stale data protection: {data.risk.stale_data_protection ? 'enabled' : 'DISABLED'}</li>
              <li>Balance mismatch protection: {data.risk.balance_mismatch_protection ? 'enabled' : 'DISABLED'}</li>
              <li>API error protection: {data.risk.api_error_protection ? 'enabled' : 'DISABLED'}</li>
              <li>Live trading: locked</li><li>Crypto / Russian market / Mixed scopes</li>
            </ul> : <p className="empty-state">Risk configuration unavailable. Safety state is unverified.</p>}
          </article>
          <article className="panel" id="settings">
            <div className="panel-header"><div><p className="eyebrow">Non-secret config</p><h2>Settings preview</h2></div><Settings size={20} /></div>
            <div className="settings-grid">
              <label>Mode <input value={data?.settings.trading_mode ?? 'unknown'} readOnly /></label>
              <label>Market scope <input value={data ? market(data.settings.market_scope) : 'unknown'} readOnly /></label>
              <label>T-Invest <input value={data ? 'sandbox' : 'unverified'} readOnly /></label>
              <label>Live execution <input value="locked — no order controls" readOnly /></label>
            </div>
            <p className="settings-note">API secrets are never shown or edited in the UI.</p>
          </article>
        </section>

        <section className="panel" id="audit">
          <div className="panel-header"><div><p className="eyebrow">Explain every action</p><h2>Audit log</h2></div><FileText size={20} /></div>
          {!data?.audit.length ? <p className="empty-state">{data ? 'No audit events recorded.' : 'Audit data unavailable.'}</p> :
            <div className="table-wrap"><table><thead><tr><th>Timestamp</th><th>Market</th><th>Event</th><th>Strategy</th><th>Reason</th></tr></thead>
              <tbody>{data.audit.map((event) => <tr key={event.id}>
                <td>{event.timestamp}</td><td>{market(event.market_scope)}</td><td>{event.event}</td><td>{event.strategy}</td><td className="reason-cell">{event.reason}</td>
              </tr>)}</tbody>
            </table></div>}
        </section>
      </main>
    </div>
  );
}
