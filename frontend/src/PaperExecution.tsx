import { useEffect, useState } from 'react';
import { getPaperFills, getPaperOrders, getReconciliation, isMockMode, simulatePaperOrder } from './apiClient';
import type { AuditEvent, Opportunity, PaperRecord, PaperReport, Venue } from './types';

export function simulationBlock(item: Opportunity, venues: Venue[], elapsed: number, maxAge: number): string {
  if (isMockMode) return 'Backend not configured; demo cannot simulate.';
  if (!item.approved || item.risk_score !== 'passed') return item.reason_text;
  if (!Number.isFinite(maxAge) || maxAge <= 0 || elapsed < 0 || item.data_age_ms + elapsed > maxAge) return 'Quote expired. Refresh data.';
  for (const name of [item.buy_venue, item.sell_venue]) {
    const venue = venues.find((v) => v.name === name && v.symbol === item.symbol);
    if (!venue || venue.status !== 'ok' || venue.live_execution || venue.mode !== 'public_read_only' || venue.depth_status !== 'available' ||
        venue.data_age_ms === null || venue.data_age_ms + elapsed > maxAge) return `${name}: fresh public depth unavailable.`;
  }
  return '';
}

function Records({ records }: { records: PaperRecord[] }) {
  return records.length ? <div className="paper-records">{records.map((record, i) => <dl key={i}>{Object.entries(record).map(([key, value]) =>
    <div key={key}><dt>{key.replace(/_/g, ' ')}</dt><dd>{value === null ? 'Unavailable' : String(value)}</dd></div>)}</dl>)}</div> : <p>No records.</p>;
}

export function PaperExecution({ opportunities, venues, audit, receivedAt, maxAge }: {
  opportunities: Opportunity[]; venues: Venue[]; audit: AuditEvent[]; receivedAt: number; maxAge: number;
}) {
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [report, setReport] = useState<PaperReport | null>(null);
  const [ledger, setLedger] = useState<{ orders: PaperRecord[]; fills: PaperRecord[]; reconciliation: PaperRecord[] } | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 100); return () => clearInterval(timer); }, []);
  useEffect(() => {
    let active = true;
    void Promise.all([getPaperOrders(), getPaperFills(), getReconciliation()]).then(([orders, fills, reconciliation]) => {
      if (active) setLedger({ orders, fills, reconciliation });
    }).catch(() => { if (active) { setLedger(null); setMessage('Paper ledger unavailable. Refresh before simulating.'); } });
    return () => { active = false; };
  }, [revision, receivedAt]);
  async function simulate(item: Opportunity) {
    const blocked = simulationBlock(item, venues, Date.now() - receivedAt, maxAge);
    if (busy || blocked || !ledger) { setMessage(blocked || 'Paper ledger unavailable.'); return; }
    setBusy(true); setMessage(''); setReport(null);
    try { setReport(await simulatePaperOrder(item)); }
    catch (cause) { setMessage(cause instanceof Error ? cause.message : 'Simulation failed.'); }
    finally { setBusy(false); setRevision((value) => value + 1); }
  }
  return <section className="panel" id="paper">
    <h2>Explainable paper simulation</h2>
    <p>Simulation uses server order books and reruns risk checks. No real orders or exchange writes. Risk score is a deterministic gate result, not a probability.</p>
    {opportunities.map((item) => {
      const blocked = simulationBlock(item, venues, now - receivedAt, maxAge);
      return <article className="paper-opportunity" key={item.id}>
        <h3>{item.symbol} · {item.buy_venue} → {item.sell_venue}</h3>
        <p>{item.summary}</p><p><strong>{item.approved ? 'Paper approved' : 'Rejected'} · Risk score: {item.risk_score}</strong></p>
        <p>{item.reason_code}: {item.reason_text}</p>
        <p>Fees {item.fees_pct.toFixed(2)}% · Slippage {item.slippage_pct.toFixed(2)}% · Net edge {item.expected_net_pct.toFixed(4)}%</p>
        <button type="button" disabled={!!blocked || busy || !ledger} onClick={() => void simulate(item)}>Simulate paper order</button>
        <small className="simulation-note">{blocked || `Paper notional: $${item.simulation_notional_usd}. Backend makes the final decision.`}</small>
        <details><summary>Advanced: quotes, depth and decision audit</summary>
          <p>Gross {item.gross_spread_pct}% − fees {item.fees_pct}% − slippage {item.slippage_pct}% = expected net {item.expected_net_pct}%.</p>
          <p>Opportunity age: {Math.round(item.data_age_ms + Math.max(0, now - receivedAt))} ms · Limit: {maxAge} ms</p>
          {venues.filter((v) => [item.buy_venue, item.sell_venue].includes(v.name)).map((v) => <p key={v.name}>
            {v.name}: bid {v.bid ?? 'unavailable'} / ask {v.ask ?? 'unavailable'} · source {v.mode} · status {v.status} · timestamp source {v.timestamp_source ?? 'unavailable'} · age at refresh {v.data_age_ms ?? 'unavailable'} ms · depth {v.depth_status} ({v.bid_levels} bid / {v.ask_levels} ask levels)
          </p>)}
          {audit.filter((event) => event.opportunity_id === item.id).slice(0, 20).map((event) => <p key={event.id}>{event.timestamp} · {event.who ?? 'Actor unavailable'} · {event.decision ?? event.event} · {event.reason} {event.execution_id && `· execution ${event.execution_id}`}</p>)}
        </details>
      </article>;
    })}
    {message && <p role="alert">{message}</p>}
    {report && <div role="status"><h3>Paper result: {report.status}</h3><p>{report.execution_id} · {report.reason_code}: {report.reason_text}</p><Records records={[report.reconciliation]} /></div>}
    <h3>Paper ledger and reconciliation</h3>
    <p>Hypothetical paired fills, not a funded portfolio. Slippage cost USD is a separate conservative reserve; depth slippage is already included in fill prices.</p>
    <button type="button" onClick={() => setRevision((value) => value + 1)}>Refresh paper ledger</button>
    {ledger ? <><details><summary>Paper orders ({ledger.orders.length})</summary><Records records={ledger.orders} /></details><details><summary>Simulated fills ({ledger.fills.length})</summary><Records records={ledger.fills} /></details><Records records={ledger.reconciliation} /></> : <p>Ledger unavailable.</p>}
  </section>;
}
