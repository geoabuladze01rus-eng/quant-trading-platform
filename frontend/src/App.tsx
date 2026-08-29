import {
  Activity,
  AlertTriangle,
  BarChart3,
  DatabaseZap,
  FileText,
  Gauge,
  Lock,
  Settings,
  ShieldCheck,
} from 'lucide-react';

import { auditEvents, exchangeHealth, opportunities, platformMode, safetyState } from './mockData';

const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});

function pct(value: number) {
  return `${value.toFixed(2)}%`;
}

export function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">QT</div>
          <div>
            <strong>Quant Platform</strong>
            <span>Arbitrage control</span>
          </div>
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
          <div>
            <p className="eyebrow">Autonomous crypto arbitrage</p>
            <h1>Command center</h1>
          </div>
          <div className="mode-cluster" aria-label="Platform state">
            <span className="status-pill paper">Mode: {platformMode}</span>
            <span className="status-pill ok">Safety: {safetyState}</span>
            <span className="status-pill locked"><Lock size={14} /> Live locked</span>
          </div>
        </header>

        <section className="metric-grid" aria-label="Portfolio and safety metrics">
          <article className="metric-card">
            <span>Portfolio value</span>
            <strong>{money.format(100000)}</strong>
            <small>Paper PnL: +{money.format(124)}</small>
          </article>
          <article className="metric-card warning">
            <span>Daily risk used</span>
            <strong>0.18% / 2.00%</strong>
            <div className="risk-track" aria-label="Daily loss limit usage"><i style={{ width: '9%' }} /></div>
          </article>
          <article className="metric-card">
            <span>Per-trade limit</span>
            <strong>{money.format(100)}</strong>
            <small>MVP safety cap</small>
          </article>
          <article className="metric-card danger-soft">
            <span>Real execution</span>
            <strong>Disabled</strong>
            <small>No live orders in MVP</small>
          </article>
        </section>

        <section className="panel-grid">
          <article className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Exchange data</p>
                <h2>Market health</h2>
              </div>
              <DatabaseZap size={20} />
            </div>
            <div className="health-list">
              {exchangeHealth.map((exchange) => (
                <div className="health-row" key={exchange.name}>
                  <span>{exchange.name}</span>
                  <b className={exchange.status === 'OK' ? 'good' : 'warn'}>{exchange.status}</b>
                  <small>{exchange.latencyMs} ms</small>
                </div>
              ))}
            </div>
          </article>

          <article className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Decision stream</p>
                <h2>Recent bot logic</h2>
              </div>
              <Activity size={20} />
            </div>
            <div className="decision-list">
              {auditEvents.slice(0, 3).map((event) => (
                <div className="decision-row" key={event.id}>
                  <span>{event.time}</span>
                  <strong>{event.event}</strong>
                  <small>{event.reason}</small>
                </div>
              ))}
            </div>
          </article>
        </section>

        <section className="panel" id="opportunities">
          <div className="panel-header">
            <div>
              <p className="eyebrow">After fees, slippage and latency</p>
              <h2>Opportunities</h2>
            </div>
            <AlertTriangle size={20} />
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Strategy</th>
                  <th>Path</th>
                  <th>Venues</th>
                  <th>Gross</th>
                  <th>Fees</th>
                  <th>Slip</th>
                  <th>Net</th>
                  <th>Notional</th>
                  <th>Age</th>
                  <th>Decision</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {opportunities.map((item) => (
                  <tr key={item.id}>
                    <td>{item.time}</td>
                    <td>{item.strategy}</td>
                    <td className="path-cell">{item.path}</td>
                    <td>{item.buyVenue} / {item.sellVenue}</td>
                    <td>{pct(item.grossPct)}</td>
                    <td>{pct(item.feesPct)}</td>
                    <td>{pct(item.slippagePct)}</td>
                    <td className={item.netPct >= 0.1 ? 'good' : 'muted'}>{pct(item.netPct)}</td>
                    <td>{money.format(item.notionalUsd)}</td>
                    <td>{item.dataAgeMs} ms</td>
                    <td><span className={`decision ${item.decision.toLowerCase()}`}>{item.decision}</span></td>
                    <td>{item.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel-grid" id="risk">
          <article className="panel">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Protections</p>
                <h2>Risk center</h2>
              </div>
              <ShieldCheck size={20} />
            </div>
            <ul className="check-list">
              <li>Live trading locked by default</li>
              <li>Daily loss limit: 2%</li>
              <li>Stale data pause</li>
              <li>Balance mismatch stop</li>
              <li>API error pause</li>
              <li>No withdrawal API permissions</li>
            </ul>
          </article>

          <article className="panel" id="settings">
            <div className="panel-header">
              <div>
                <p className="eyebrow">Non-secret config</p>
                <h2>Settings preview</h2>
              </div>
              <Settings size={20} />
            </div>
            <div className="settings-grid">
              <label>Mode <input value="paper" readOnly /></label>
              <label>Max daily loss <input value="2%" readOnly /></label>
              <label>Min net edge <input value="0.10%" readOnly /></label>
              <label>Max notional <input value="$100" readOnly /></label>
            </div>
            <p className="settings-note">API secrets are never shown or edited in the UI.</p>
          </article>
        </section>

        <section className="panel" id="audit">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Explain every action</p>
              <h2>Audit log</h2>
            </div>
            <FileText size={20} />
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                  <th>Strategy</th>
                  <th>Net edge</th>
                  <th>Decision</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {auditEvents.map((event) => (
                  <tr key={event.id}>
                    <td>{event.time}</td>
                    <td>{event.event}</td>
                    <td>{event.strategy}</td>
                    <td>{event.netEdge === undefined ? '-' : pct(event.netEdge)}</td>
                    <td>{event.decision}</td>
                    <td>{event.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}
