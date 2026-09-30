import { AlertTriangle, ArrowRight, CircleDollarSign, ReceiptText, WalletCards } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import {
  cancelOrder, createOrder, getAuditPage, getOrder, getPaperAccount, getPaperBalances,
  getPaperPerformance, getPaperPositions, getPaperReconciliation, getResidualExposure,
  getSavedPaperFills, getSavedPaperOrders, paperConfigured, PaperRequestError, previewOrder,
} from './paperApi';
import type { Account, Balance, CommandResult, Json, PaperOrder, Reconciliation, ResidualObservation, Row } from './paperApi';
import { simulationBlock } from './paperSafety';
import type { AuditEvent, Opportunity, Venue } from './types';

const ASSETS = ['USDT', 'BTC', 'ETH', 'LTC'] as const;
const label = (key: string) => key.replace(/_/g, ' ');
const display = (value: Json | undefined): string => value === null || value === undefined ? 'Недоступно' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const venueName = (value: Json | undefined) => ({ binance: 'Binance', bybit: 'Bybit', okx: 'OKX' }[String(value)] ?? display(value));

interface Section<T> { data: T | null; error: string }
const pending = <T,>(): Section<T> => ({ data: null, error: '' });
async function section<T>(promise: Promise<T>, error: string): Promise<Section<T>> {
  try { return { data: await promise, error: '' }; }
  catch { return { data: null, error }; }
}
interface PortfolioSections {
  account: Section<Account>;
  balances: Section<Balance[]>;
  orders: Section<PaperOrder[]>;
  fills: Section<Row[]>;
  positions: Section<Row[]>;
  performance: Section<Row>;
  reconciliation: Section<Reconciliation>;
}
const initialSections = (): PortfolioSections => ({
  account: pending(), balances: pending(), orders: pending(), fills: pending(), positions: pending(), performance: pending(), reconciliation: pending(),
});

function Records({ records }: { records: Row[] }) {
  return records.length ? <div className="paper-records">{records.map((row, index) => <dl key={index}>{Object.entries(row).map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{display(value)}</dd></div>)}</dl>)}</div> : <p className="empty-copy">Записей пока нет.</p>;
}

function AuditBrowser() {
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState('');
  const [reason, setReason] = useState('');
  const [page, setPage] = useState<Awaited<ReturnType<typeof getAuditPage>> | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setPage(null);
    void getAuditPage(offset, reason ? { reason_code: reason } : {}).then((value) => { if (active) { setPage(value); setError(''); } }).catch(() => { if (active) setError('Persistent audit недоступен.'); });
    return () => { active = false; };
  }, [offset, reason]);
  return <details className="advanced-block"><summary>Advanced: persistent audit</summary>
    <form className="filter-form" onSubmit={(event) => { event.preventDefault(); setOffset(0); setReason(filter.trim()); }}>
      <label>Reason code<input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="например insufficient_net_edge" /></label><button>Фильтровать</button>
    </form>
    {error && <p role="alert" className="inline-alert">{error}</p>}
    {page ? <><p className="empty-copy">{page.total} events · offset {page.offset}</p><Records records={page.items} /><div className="button-row"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Назад</button><button disabled={offset + page.items.length >= page.total} onClick={() => setOffset(offset + 50)}>Дальше</button></div></> : <p className="empty-copy">Audit загружается…</p>}
  </details>;
}

export function PaperAlpha({ opportunities, venues, audit, receivedAt, maxAge, actionsEnabled }: {
  opportunities: Opportunity[]; venues: Venue[]; audit: AuditEvent[]; receivedAt: number; maxAge: number; actionsEnabled: boolean;
}) {
  const [now, setNow] = useState(Date.now());
  const [revision, setRevision] = useState(0);
  const [portfolio, setPortfolio] = useState<PortfolioSections>(initialSections);
  const [residuals, setResiduals] = useState<Section<ResidualObservation[]>>(pending);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const busyRef = useRef(false);
  const [preview, setPreview] = useState<{ item: Opportunity; result: CommandResult } | null>(null);
  const [result, setResult] = useState<CommandResult | null>(null);
  const [detail, setDetail] = useState<PaperOrder | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [requestKey, setRequestKey] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(timer); }, []);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    if (!paperConfigured) return () => { active = false; };

    async function loadPortfolio() {
      if (!active) return;
      setPreview(null);
      setPortfolio(initialSections());
      setResiduals(pending());
      const [
        account, balances, orders, fills, positions, performance,
        reconciliation, residualExposure,
      ] = await Promise.all([
        section(getPaperAccount(), 'Account недоступен'),
        section(getPaperBalances(), 'Balances недоступны'),
        section(getSavedPaperOrders(), 'Сделки недоступны'),
        section(getSavedPaperFills(), 'Fills недоступны'),
        section(getPaperPositions(), 'Positions недоступны'),
        section(getPaperPerformance(), 'P&L недоступен'),
        section(getPaperReconciliation(), 'Reconciliation недоступен'),
        section(getResidualExposure(), 'Residual exposure недоступен'),
      ]);
      if (!active) return;
      setPortfolio({
        account, balances, orders, fills, positions, performance, reconciliation,
      });
      setResiduals(residualExposure);
      timer = setTimeout(() => void loadPortfolio(), 10000);
    }

    void loadPortfolio();
    return () => {
      active = false;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [revision]);
  useEffect(() => { if (preview) dialog.current?.showModal(); else if (dialog.current?.open) dialog.current.close(); }, [preview]);

  const reconciliationOk = portfolio.reconciliation.data?.status === 'ok';
  const accountActive = portfolio.account.data?.status === 'active';
  const residualsFlat = !!residuals.data && residuals.data.every((row) => row.decision.status === 'flat');
  const portfolioReady = !!portfolio.account.data && !!portfolio.balances.data && reconciliationOk;

  async function showPreview(item: Opportunity) {
    const blocked = simulationBlock(item, venues, Date.now() - receivedAt, maxAge);
    if (busyRef.current || blocked || !actionsEnabled || !portfolioReady || !residualsFlat || uncertain) return;
    busyRef.current = true; setBusy(true); setMessage('');
    try { setPreview({ item, result: await previewOrder(item) }); }
    catch (cause) { setMessage(cause instanceof Error ? cause.message : 'Preview недоступен.'); }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function mutate(item?: Opportunity, cancelId?: string) {
    if (busyRef.current || uncertain || !portfolioReady || !paperConfigured || !actionsEnabled) return;
    const key = crypto.randomUUID();
    setRequestKey(key); busyRef.current = true; setBusy(true); setMessage(''); setResult(null);
    try { setResult(cancelId ? await cancelOrder(cancelId, key) : await createOrder(item!, key)); setPreview(null); }
    catch (cause) {
      setUncertain(cause instanceof PaperRequestError ? cause.uncertain : true);
      setPreview(null); setMessage(cause instanceof Error ? cause.message : 'Результат запроса неизвестен.');
    } finally { busyRef.current = false; setBusy(false); setRevision((value) => value + 1); }
  }

  const account = portfolio.account.data;
  const performance = portfolio.performance.data;
  const balances = portfolio.balances.data ?? [];
  const cashBalance = balances.find((item) => item.asset === 'USDT');
  const displayedFunds = account?.virtual_equity_usdt ?? cashBalance?.total;
  const fundsAreCashOnly = account?.virtual_equity_usdt === null && !!cashBalance;
  const orders = portfolio.orders.data ?? [];
  const fills = portfolio.fills.data ?? [];
  const approved = opportunities.filter((item) => item.approved);
  const strategyRealized = account?.strategy_realized_pnl_usdt ?? account?.realized_pnl_usdt;
  const strategyUnrealized = account?.strategy_unrealized_pnl_usdt ?? account?.unrealized_pnl_usdt;
  const portfolioErrors = Object.values(portfolio).map((item) => item.error).filter(Boolean);

  return <section className="paper-section" id="paper">
    <div className="section-heading"><div><p className="eyebrow">Только виртуальный учёт</p><h2>Виртуальные деньги и paper-сделки</h2></div><button className="secondary-button" disabled={busy || !paperConfigured} onClick={() => setRevision((value) => value + 1)}>Обновить portfolio</button></div>
    {!paperConfigured && <div className="inline-alert neutral" role="status">Demo mode: portfolio и fills не выдумываются. Укажите VITE_API_BASE_URL.</div>}
    {portfolioErrors.length > 0 && <div className="inline-alert warning" role="alert">{portfolioErrors.join(' · ')}. Доступные секции показаны ниже.</div>}
    {message && <div className="inline-alert warning" role="alert">{message}</div>}
    {uncertain && <div className="inline-alert danger" role="alert">Paper-действия приостановлены после неизвестного исхода. Сверьте ledger и request key {requestKey}.</div>}

    <div className="money-grid">
      <article className="money-card primary-money">
        <div className="card-icon"><CircleDollarSign size={21} /></div><small>Виртуальные деньги</small>
        <strong>{display(displayedFunds)} <em>USDT</em></strong><p>{fundsAreCashOnly ? 'Денежный остаток без оценки BTC, ETH и LTC.' : 'Не являются реальными средствами.'}</p>
      </article>
      <article className="money-card"><small>Strategy realized P&amp;L</small><strong className={Number(strategyRealized ?? 0) >= 0 ? 'value-positive' : 'value-danger'}>{display(strategyRealized)} USDT</strong><p>Только закрытые сделки робота</p></article>
      <article className="money-card"><small>Strategy unrealized P&amp;L</small><strong>{display(strategyUnrealized)} USDT</strong><p>Без исходных BTC, ETH и LTC</p></article>
      <article className="money-card"><small>Fees + slippage</small><strong>{display(account?.fees_paid_usdt)} + {display(account?.slippage_cost_usdt)}</strong><p>Виртуальные расходы, USDT</p></article>
    </div>

    <div className="portfolio-grid">
      <article className="panel balance-panel">
        <div className="panel-title"><div><WalletCards size={19} /><h3>Balances</h3></div><span className={`state-chip ${reconciliationOk ? 'positive' : 'warning'}`}>{reconciliationOk ? 'Reconciled' : 'Не подтверждено'}</span></div>
        <div className="balance-list">{ASSETS.map((asset) => {
          const balance = balances.find((item) => item.asset === asset);
          return <div className="balance-row" key={asset}><div className={`asset-icon ${asset.toLowerCase()}`}>{asset.slice(0, 1)}</div><div><strong>{asset}</strong><span>Доступно {balance?.available ?? '—'}</span></div><strong>{balance?.total ?? '—'}</strong></div>;
        })}</div>
      </article>

      <article className="panel performance-panel">
        <div className="panel-title"><div><ReceiptText size={19} /><h3>Учёт результата</h3></div></div>
        <dl className="performance-list">
          <div><dt>Daily P&amp;L</dt><dd>{display(performance?.daily_pnl_usdt)} USDT</dd></div>
          <div><dt>Filled orders</dt><dd>{display(performance?.filled_orders)}</dd></div>
          <div><dt>Rejected orders</dt><dd>{display(performance?.rejected_orders)}</dd></div>
          <div><dt>Open positions</dt><dd>{portfolio.positions.data?.filter((position) => position.status === 'open').length ?? '—'}</dd></div>
          <div><dt>Directional model</dt><dd>{Array.isArray(performance?.strategy_by_symbol) ? `${performance.strategy_by_symbol.length} pairs` : '—'}</dd></div>
        </dl>
        {account && account.status !== 'active' && <div className="inline-alert danger">Account остановлен из-за accounting mismatch.</div>}
      </article>
    </div>

    <div className="section-heading compact"><div><p className="eyebrow">Persistent ledger</p><h3>Последние paper-сделки</h3></div><span>{orders.length} orders · {fills.length} fills</span></div>
    <div className="trade-list">
      {orders.length ? orders.slice(0, 20).map((order) => {
        const orderFills = fills.filter((fill) => fill.order_id === order.id);
        return <article className="trade-card" key={order.id}>
          <div className="trade-main"><div><strong>{order.symbol}</strong><span>{order.side === 'spread' ? <>{venueName(order.buy_venue)} <ArrowRight size={13} /> {venueName(order.sell_venue)}</> : `${venueName(order.venue)} · ${String(order.side).toUpperCase()}`}</span></div><div><small>Notional</small><strong>{order.notional_usdt} USDT</strong></div><span className={`state-chip ${order.status === 'filled' ? 'positive' : order.status === 'rejected' || order.status === 'failed' ? 'danger' : 'warning'}`}>{label(order.status)}</span></div>
          <p className="reason-line"><span>{order.status === 'rejected' ? 'Причина отказа' : 'Результат'}</span>{order.human_reason} · {order.reason_code}</p>
          <details><summary>Метрики, fills и идентификаторы</summary><Records records={[order, ...orderFills]} /></details>
          <div className="button-row"><button className="text-button" onClick={() => void getOrder(order.id).then(setDetail).catch(() => setMessage('Детали order недоступны.'))}>Проверить order</button>{['created', 'accepted', 'partially_filled'].includes(order.status) && <button className="text-button danger-text" disabled={busy || uncertain || !actionsEnabled} onClick={() => void mutate(undefined, order.id)}>Отменить остаток paper-order</button>}</div>
        </article>;
      }) : <div className="empty-card"><ReceiptText size={22} /><strong>Paper-сделок пока нет</strong><p>Ledger остаётся пустым, пока backend не подтвердит виртуальную сделку.</p></div>}
    </div>
    {detail && <details className="advanced-block" open><summary>Выбранный order</summary><Records records={[detail]} /></details>}

    <div className="section-heading compact"><div><p className="eyebrow">Preview с повторной risk-проверкой</p><h3>Paper-возможности</h3></div><span>{approved.length} доступны</span></div>
    <div className="preview-grid">
      {approved.length ? approved.map((item) => {
        const blocked = !actionsEnabled ? 'Safety state не подтверждён.' : simulationBlock(item, venues, now - receivedAt, maxAge) || residuals.error;
        return <article className="preview-card" key={item.id}><div><strong>{item.symbol}</strong><span>{venueName(item.buy_venue)} → {venueName(item.sell_venue)}</span></div><p>Net edge <strong>{item.expected_net_pct.toFixed(4)}%</strong> · fees {item.fees_pct.toFixed(2)}%</p><button disabled={!!blocked || busy || !portfolioReady || !residualsFlat || uncertain || !accountActive} onClick={() => void showPreview(item)}>Открыть paper-preview</button><small>{blocked || `Виртуальный notional ${item.simulation_notional_usd} USDT`}</small></article>;
      }) : <p className="empty-copy">Нет свежих risk-approved возможностей.</p>}
    </div>

    <details className="advanced-block"><summary>Advanced: residual exposure, positions и accounting</summary>
      {residuals.error && <p className="inline-alert warning">{residuals.error}</p>}
      {residuals.data?.map(({ execution_group_id, symbol, decision }) => <div className={`residual-state residual-${decision.status}`} key={execution_group_id}><strong>{symbol} · {decision.status}</strong><p>{decision.human_reason} · {decision.reason_code}</p><p>Остаток {decision.residual_quantity} · оценка {decision.residual_notional_usd ?? 'нет mark'} USDT.</p></div>)}
      <h4>Positions</h4><Records records={portfolio.positions.data ?? []} />
      <h4>Reconciliation issues</h4><Records records={portfolio.reconciliation.data?.issues ?? []} />
      <h4>Связанный decision audit</h4>{audit.slice(0, 10).map((event) => <p className="audit-inline" key={event.id}>{event.timestamp} · {event.event} · {event.reason}</p>)}
    </details>
    {paperConfigured && <AuditBrowser />}

    <dialog ref={dialog} aria-labelledby="paper-confirm-title" onCancel={() => { if (!busy) setPreview(null); }}>
      {preview && <div className="dialog-content"><span className="state-chip paper">PAPER ONLY</span><h2 id="paper-confirm-title">Подтвердить виртуальную сделку</h2><p>{preview.item.symbol} · {venueName(preview.item.buy_venue)} → {venueName(preview.item.sell_venue)} · {preview.item.simulation_notional_usd} USDT</p><div className="inline-alert neutral">{preview.result.order.human_reason} · {preview.result.order.reason_code}</div><p>Backend заново проверит books, balances и risk limits. Реальный exchange order не создаётся.</p><div className="button-row"><button disabled={busy || preview.result.order.status === 'rejected' || preview.result.reconciliation.status === 'error'} onClick={() => void mutate(preview.item)}>Подтвердить PAPER order</button><button className="secondary-button" disabled={busy} onClick={() => setPreview(null)}>Назад</button></div></div>}
    </dialog>
    {result && <div className="inline-alert positive" role="status"><strong>Paper order: {label(result.order.status)}</strong><br />{result.order.human_reason} · {result.order.reason_code}</div>}
    {requestKey && <p className="request-key">Последний request key: <code>{requestKey}</code></p>}
  </section>;
}
