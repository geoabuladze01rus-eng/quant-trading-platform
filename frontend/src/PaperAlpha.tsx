import { useEffect, useRef, useState } from 'react';
import { cancelOrder, createOrder, getAuditPage, getOrder, getPortfolio, getResidualExposure, paperConfigured, previewOrder } from './paperApi';
import type { CommandResult, Json, PaperOrder, Portfolio, ResidualObservation, Row } from './paperApi';
import type { AuditEvent, Opportunity, Venue } from './types';
import { simulationBlock } from './PaperExecution';

const fieldLabels: Record<string, string> = { account_id: 'Счёт', asset: 'Актив', available: 'Доступно', reserved: 'Зарезервировано', total: 'Всего', status: 'Статус', reason_code: 'Код причины', human_reason: 'Объяснение', quantity: 'Количество', side: 'Направление', venue: 'Площадка', price: 'Цена', notional_usd: 'Сумма, USD', fee_usd: 'Комиссия', timestamp: 'Время', order_id: 'Номер симуляции', decision: 'Решение', actor: 'Участник', event_type: 'Событие' };\nconst label = (key: string) => fieldLabels[key] ?? key.replace(/_/g, ' ');
const display = (value: Json): string => value === null ? 'Не указано' : typeof value === 'object' ? JSON.stringify(value) : String(value);
function Records({ records }: { records: Row[] }) {
  return records.length ? <div className="paper-records">{records.map((row, i) => <dl key={i}>{Object.entries(row).map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{display(value)}</dd></div>)}</dl>)}</div> : <p>No records yet.</p>;
}
function AuditBrowser() {
  const [offset, setOffset] = useState(0), [filter, setFilter] = useState(''), [reason, setReason] = useState('');
  const [page, setPage] = useState<Awaited<ReturnType<typeof getAuditPage>> | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { let active = true; setPage(null); void getAuditPage(offset, reason ? { reason_code: reason } : {}).then((value) => { if (active) { setPage(value); setError(''); } }).catch(() => { if (active) setError('Журнал решений недоступен.'); }); return () => { active = false; }; }, [offset, reason]);
  return <details><summary>Advanced: persistent decision audit</summary>
    <form onSubmit={(event) => { event.preventDefault(); setOffset(0); setReason(filter.trim()); }}><label>Reason code <input value={filter} onChange={(event) => setFilter(event.target.value)} /></label><button>Filter audit</button></form>
    {error && <p role="alert">{error}</p>}{page ? <><p>{page.total} events · offset {page.offset}</p><Records records={page.items} /><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button><button disabled={offset + page.items.length >= page.total} onClick={() => setOffset(offset + 50)}>Next</button></> : <p>Loading audit…</p>}
  </details>;
}
export function PaperAlpha({ opportunities, venues, audit, receivedAt, maxAge }: { opportunities: Opportunity[]; venues: Venue[]; audit: AuditEvent[]; receivedAt: number; maxAge: number }) {
  const [now, setNow] = useState(Date.now()), [revision, setRevision] = useState(0);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [residuals, setResiduals] = useState<ResidualObservation[] | null>(null);
  const [residualError, setResidualError] = useState('');
  const [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  const busyRef = useRef(false);
  const [preview, setPreview] = useState<{ item: Opportunity; result: CommandResult } | null>(null);
  const [result, setResult] = useState<CommandResult | null>(null);
  const [detail, setDetail] = useState<PaperOrder | null>(null);
  const [uncertain, setUncertain] = useState(false), [requestKey, setRequestKey] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 100); return () => clearInterval(timer); }, []);
  useEffect(() => { let active = true; setPortfolio(null); if (paperConfigured) void getPortfolio().then((value) => { if (active) setPortfolio(value); }).catch((cause) => { if (active) setMessage(String(cause.message)); }); return () => { active = false; }; }, [revision]);
  useEffect(() => { let active = true; setResiduals(null); if (paperConfigured) void getResidualExposure().then((value) => { if (active) { setResiduals(value); setResidualError(''); } }).catch(() => { if (active) setResidualError('Проверка остатка недоступна. Предварительный расчёт приостановлен.'); }); return () => { active = false; }; }, [revision]);
  useEffect(() => { if (preview) dialog.current?.showModal(); else dialog.current?.close(); }, [preview]);
  async function showPreview(item: Opportunity) {
    if (busyRef.current || simulationBlock(item, venues, Date.now() - receivedAt, maxAge) || !portfolio || !residuals || residuals.some((row) => row.decision.status !== 'flat') || uncertain) return;
    busyRef.current = true; setBusy(true); setMessage('');
    try { setPreview({ item, result: await previewOrder(item) }); } catch (cause) { setMessage(cause instanceof Error ? cause.message : 'Предварительный расчёт недоступен.'); }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function mutate(item?: Opportunity, cancelId?: string) {
    if (busyRef.current || uncertain || !portfolio || !paperConfigured) return;
    const key = crypto.randomUUID(); setRequestKey(key); busyRef.current = true; setBusy(true); setMessage(''); setResult(null);
    try { setResult(cancelId ? await cancelOrder(cancelId, key) : await createOrder(item!, key)); setPreview(null); }
    catch (cause) { setUncertain(true); setPreview(null); setMessage(cause instanceof Error ? cause.message : 'Результат неизвестен. Проверьте журнал перед повтором.'); }
    finally { busyRef.current = false; setBusy(false); setRevision((v) => v + 1); }
  }
  return <section className="panel" id="paper"><h2>Paper Alpha · virtual portfolio</h2>
    <p>PAPER ONLY. Virtual balances and simulated fills. No exchange orders, real money or promised returns. Records reload from the backend after a restart.</p>
    {!paperConfigured && <p role="status">Demo mode · backend disabled. No portfolio records or fills are fabricated.</p>}
    <button disabled={busy || !paperConfigured} onClick={() => setRevision((v) => v + 1)}>Refresh saved portfolio</button>
    {message && <p role="alert">{message}</p>}{requestKey && <p>Last request key: <code>{requestKey}</code></p>}
    {uncertain && <p role="alert">Actions paused after an unknown outcome. Inspect the saved ledger and request key before starting a new request.</p>}
    {paperConfigured && !portfolio && <p>Portfolio loading or unavailable. Paper actions disabled.</p>}
    {portfolio && <><div className="metric-grid">
      {([['Виртуальные средства', portfolio.account.virtual_equity_usdt], ['Реализованный результат', portfolio.account.realized_pnl_usdt], ['Нереализованный результат', portfolio.account.unrealized_pnl_usdt], ['Результат за день', portfolio.performance.daily_pnl_usdt ?? null], ['Комиссии', portfolio.account.fees_paid_usdt], ['Проскальзывание', portfolio.account.slippage_cost_usdt]] as [string, Json][]).map(([name, value]) => <article className="metric-card" key={name}><span>{name}</span><strong>{display(value)}</strong><small>Virtual USDT</small></article>)}
      </div><p>{portfolio.positions.filter((p) => p.status === 'open').length} open positions · {portfolio.orders.filter((o) => o.status === 'rejected').length} rejected orders</p>
      <div className="table-wrap"><table><caption>Virtual balances</caption><thead><tr><th>Asset</th><th>Available</th><th>Reserved</th><th>Total</th></tr></thead><tbody>{portfolio.balances.map((b) => <tr key={b.asset}><td>{b.asset}</td><td>{b.available}</td><td>{b.reserved}</td><td>{b.total}</td></tr>)}</tbody></table></div>
      <p role={portfolio.reconciliation.status === 'error' ? 'alert' : 'status'}>Reconciliation: {portfolio.reconciliation.status}</p>
      {portfolio.account.status !== 'active' && <p role="alert">Paper account is halted after an accounting mismatch. New paper orders are blocked until the ledger is repaired.</p>}
      {Array.isArray(portfolio.account.unpriced_pnl_assets) && portfolio.account.unpriced_pnl_assets.length > 0 && <p role="status">Unrealized PnL is unavailable for initial inventory with unknown cost basis. The asset list is shown in Advanced accounting.</p>}
      <Records records={portfolio.reconciliation.issues} /></>}
    <section className="residual-review" aria-label="Residual exposure review">
      <h3>Остаточная экспозиция · PAPER ONLY</h3>
      <p>Реальные деньги не используются. Live locked. Защитный paper-хедж здесь не исполняется автоматически.</p>
      {residualError && <p role="alert">{residualError}</p>}
      {paperConfigured && !residuals && !residualError && <p>Проверка остатка загружается; preview временно заблокирован.</p>}
      {residuals?.length === 0 && <p role="status">Сохранённых исполнений пока нет.</p>}
      {residuals?.map(({ execution_group_id, symbol, decision }) => <article className={`residual-state residual-${decision.status}`} key={execution_group_id} role={decision.status === 'flat' ? 'status' : 'alert'}>
        <strong>{symbol} · {decision.status === 'flat' ? 'Объёмы двух сторон совпадают' : decision.status === 'hedge_required' ? 'Остаток требует проверки' : 'Симуляция остановлена — проверьте учёт и данные'}</strong>
        <p>{decision.human_reason} · {decision.reason_code}</p>
        <p>Нога: {decision.filled_leg} · остаток {decision.residual_quantity} · оценка {decision.residual_notional_usd ?? 'нет свежей цены'} USDT · лимит {decision.configured_cap_usd} USDT.</p>
        <p>Mark: {decision.mark_source ?? 'не подтверждён'} · свежесть {decision.mark_age_ms === null ? 'недоступна' : `${decision.mark_age_ms} ms`}.</p>
        {decision.status === 'hedge_required' && <p>Предполагаемое направление: {decision.hedge_side}. {decision.reason_not_executed}</p>}
        {decision.status === 'halted' && <p>Действие: не отправляйте новые paper-команды; проверьте audit, источник mark и reconciliation.</p>}
      </article>)}
    </section>
    <h3>Preview a paper opportunity</h3>{!opportunities.length && <p>No opportunities from current sources. Wait for healthy market data.</p>}
    {opportunities.map((item) => { const blocked = simulationBlock(item, venues, now - receivedAt, maxAge); return <article className="paper-opportunity" key={item.id}>
      <h4>{item.symbol} · {item.buy_venue} → {item.sell_venue}</h4><p>{item.summary}</p><p>{item.reason_code}: {item.reason_text}</p>
      <p>Risk gate: {item.risk_score} · fees {item.fees_pct.toFixed(2)}% · slippage {item.slippage_pct.toFixed(2)}% · expected net {item.expected_net_pct.toFixed(4)}%</p>
      <button disabled={!!blocked || busy || !portfolio || !residuals || residuals.some((row) => row.decision.status !== 'flat') || uncertain || portfolio.reconciliation.status === 'error' || portfolio.account.status !== 'active'} onClick={() => void showPreview(item)}>Preview paper order</button><p>{blocked || residualError || `Virtual notional ${item.simulation_notional_usd} USDT. Preview does not save an order.`}</p>
      <details><summary>Advanced: sources and decision</summary><p>Gross {item.gross_spread_pct}% · age {Math.round(item.data_age_ms + Math.max(0, now - receivedAt))} ms · maximum {maxAge} ms.</p>{venues.filter((v) => [item.buy_venue, item.sell_venue].includes(v.name)).map((v) => <p key={v.name}>{v.name} · {v.status} · bid {v.bid ?? 'нет данных'} / ask {v.ask ?? 'нет данных'} · timestamp {v.timestamp_source ?? 'неизвестно'} · depth {v.depth_status}, {v.bid_levels}/{v.ask_levels} levels</p>)}{audit.filter((event) => event.opportunity_id === item.id).slice(0, 10).map((event) => <p key={event.id}>{event.timestamp} · {event.who} · {event.reason}</p>)}</details>
    </article>; })}
    <dialog ref={dialog} aria-labelledby="paper-confirm-title" onCancel={() => { if (!busy) setPreview(null); }}>{preview && <><h2 id="paper-confirm-title">Confirm PAPER ONLY order</h2><p>Virtual notional {preview.item.simulation_notional_usd} USDT · {preview.item.symbol}</p><p>{preview.result.order.reason_code}: {preview.result.order.human_reason}</p><p>Preview status: {preview.result.order.status}. Confirmation reruns backend checks against current books and balances.</p><Records records={[preview.result.explanation]} /><button disabled={busy || preview.result.order.status === 'rejected' || preview.result.reconciliation.status === 'error'} onClick={() => void mutate(preview.item)}>Confirm paper order</button><button disabled={busy} onClick={() => setPreview(null)}>Back without creating</button></>}</dialog>
    {result && <div role="status"><h3>Paper order {label(result.order.status)}</h3><p>{result.order.reason_code}: {result.order.human_reason}</p><p>Order {result.order.id}</p><Records records={result.reconciliation.issues} /></div>}
    {portfolio && <><h3>Saved orders</h3>{!portfolio.orders.length ? <p>No saved orders yet.</p> : portfolio.orders.slice(0, 100).map((order) => <article className="paper-opportunity" key={order.id}><strong>{order.symbol} · {label(order.status)}</strong><p>{order.reason_code}: {order.human_reason}</p><button onClick={() => void getOrder(order.id).then(setDetail).catch(() => setMessage('Подробности недоступны.'))}>Inspect order</button>{['created', 'accepted', 'partially_filled'].includes(order.status) && <button disabled={busy || uncertain} onClick={() => void mutate(undefined, order.id)}>Cancel remaining paper quantity</button>}</article>)}
      {detail && <details open><summary>Selected order</summary><Records records={[detail]} /></details>}
      <details><summary>Advanced: fills, positions and accounting</summary><h4>Simulated fills</h4><Records records={portfolio.fills} /><h4>Positions</h4><Records records={portfolio.positions} /><h4>Performance</h4><Records records={[portfolio.performance]} /></details><AuditBrowser /></>}
  </section>;
}
