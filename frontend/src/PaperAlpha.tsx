import { useEffect, useRef, useState } from 'react';
import { Activity, ArrowRight, ChevronDown, CircleAlert, LockKeyhole, RefreshCw } from 'lucide-react';

import { cancelOrder, createOrder, getAuditPage, getOrder, getPortfolio, getResidualExposure, paperConfigured, previewOrder } from './paperApi';
import type { CommandResult, Json, PaperOrder, Portfolio, ResidualObservation, Row } from './paperApi';
import type { AuditEvent, Opportunity, Venue } from './types';
import { simulationBlock } from './PaperExecution';

const fieldLabels: Record<string, string> = {
  account_id: 'Счёт', asset: 'Актив', available: 'Доступно', reserved: 'Зарезервировано',
  total: 'Всего', status: 'Статус', reason_code: 'Код причины', human_reason: 'Объяснение',
  quantity: 'Количество', side: 'Направление', venue: 'Площадка', price: 'Цена',
  notional_usd: 'Сумма, USD', fee_usd: 'Комиссия', timestamp: 'Время',
  order_id: 'Номер симуляции', decision: 'Решение', actor: 'Участник', event_type: 'Событие',
};
const statusLabels: Record<string, string> = {
  created: 'Создано', accepted: 'Принято в симуляции', partially_filled: 'Исполнено частично',
  filled: 'Смоделировано', cancelled: 'Остаток отменён', rejected: 'Отклонено', failed: 'Ошибка',
};
const label = (key: string) => fieldLabels[key] ?? statusLabels[key] ?? key.replace(/_/g, ' ');
const display = (value: Json): string =>
  value === null ? 'Не указано' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const venueLabel = (name: string) => ({
  binance: 'Binance', bybit: 'Bybit', okx: 'OKX', t_invest: 'Т‑Инвестиции',
}[name] ?? name);

function Records({ records }: { records: Row[] }) {
  return records.length ? (
    <div className="paper-records">
      {records.map((row, index) => (
        <dl key={index}>
          {Object.entries(row).map(([key, value]) => (
            <div key={key}><dt>{label(key)}</dt><dd>{display(value)}</dd></div>
          ))}
        </dl>
      ))}
    </div>
  ) : <p className="empty-state">Записей пока нет.</p>;
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
    void getAuditPage(offset, reason ? { reason_code: reason } : {})
      .then((value) => {
        if (active) { setPage(value); setError(''); }
      })
      .catch(() => { if (active) setError('Журнал решений недоступен.'); });
    return () => { active = false; };
  }, [offset, reason]);

  return (
    <details className="advanced-panel">
      <summary>Подробный журнал решений <ChevronDown size={16} /></summary>
      <form className="audit-filter" onSubmit={(event) => {
        event.preventDefault();
        setOffset(0);
        setReason(filter.trim());
      }}>
        <label>Фильтр по причине
          <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Например: stale_data" />
        </label>
        <button className="button button-secondary" type="submit">Показать</button>
      </form>
      {error && <p role="alert" className="inline-alert">{error}</p>}
      {page ? <>
        <p className="muted">Всего событий: {page.total}</p>
        <Records records={page.items} />
        <div className="pagination">
          <button className="button button-secondary" disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 50))}>Назад</button>
          <button className="button button-secondary" disabled={offset + page.items.length >= page.total}
            onClick={() => setOffset(offset + 50)}>Дальше</button>
        </div>
      </> : !error && <p className="muted">Загружаем журнал…</p>}
    </details>
  );
}

export function PaperAlpha({
  opportunities, venues, audit, receivedAt, maxAge,
}: {
  opportunities: Opportunity[];
  venues: Venue[];
  audit: AuditEvent[];
  receivedAt: number;
  maxAge: number;
}) {
  const [now, setNow] = useState(Date.now());
  const [revision, setRevision] = useState(0);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [residuals, setResiduals] = useState<ResidualObservation[] | null>(null);
  const [residualError, setResidualError] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const busyRef = useRef(false);
  const [preview, setPreview] = useState<{ item: Opportunity; result: CommandResult } | null>(null);
  const [result, setResult] = useState<CommandResult | null>(null);
  const [detail, setDetail] = useState<PaperOrder | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [requestKey, setRequestKey] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let active = true;
    setPortfolio(null);
    if (paperConfigured) void getPortfolio().then((value) => {
      if (active) setPortfolio(value);
    }).catch((cause) => {
      if (active) setMessage(cause instanceof Error ? cause.message : 'Портфель недоступен.');
    });
    return () => { active = false; };
  }, [revision]);
  useEffect(() => {
    let active = true;
    setResiduals(null);
    if (paperConfigured) void getResidualExposure().then((value) => {
      if (active) { setResiduals(value); setResidualError(''); }
    }).catch(() => {
      if (active) setResidualError('Проверка остатка недоступна. Предварительный расчёт приостановлен.');
    });
    return () => { active = false; };
  }, [revision]);
  useEffect(() => {
    if (preview) dialog.current?.showModal();
    else dialog.current?.close();
  }, [preview]);

  async function showPreview(item: Opportunity) {
    if (busyRef.current || simulationBlock(item, venues, Date.now() - receivedAt, maxAge) ||
        !portfolio || !residuals || residuals.some((row) => row.decision.status !== 'flat') || uncertain) return;
    busyRef.current = true;
    setBusy(true);
    setMessage('');
    try {
      setPreview({ item, result: await previewOrder(item) });
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : 'Предварительный расчёт недоступен.');
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function mutate(item?: Opportunity, cancelId?: string) {
    if (busyRef.current || uncertain || !portfolio || !paperConfigured) return;
    const key = crypto.randomUUID();
    setRequestKey(key);
    busyRef.current = true;
    setBusy(true);
    setMessage('');
    setResult(null);
    try {
      setResult(cancelId ? await cancelOrder(cancelId, key) : await createOrder(item!, key));
      setPreview(null);
    } catch (cause) {
      setUncertain(true);
      setPreview(null);
      setMessage(cause instanceof Error ? cause.message : 'Результат неизвестен. Проверьте журнал перед повтором.');
    } finally {
      busyRef.current = false;
      setBusy(false);
      setRevision((value) => value + 1);
    }
  }

  return (
    <section className="panel paper-panel" id="paper">
      <div className="panel-header">
        <div><p className="eyebrow">Виртуальный счёт</p><h2>Paper-портфель</h2></div>
        <span className="paper-only-badge"><LockKeyhole size={14} /> Только симуляция</span>
      </div>
      <p className="panel-lead">Используются условные деньги и расчётные исполнения. Ордера на биржу не отправляются; результат не обещает прибыль.</p>
      {!paperConfigured && <div className="info-callout"><CircleAlert size={17} /><p><strong>Демо-режим</strong><br />Сервер портфеля не подключён. Баланс и сделки не выдумываются.</p></div>}
      {message && <p role="alert" className="inline-alert">{message}</p>}
      {requestKey && <p className="muted">Ключ последнего запроса: <code>{requestKey}</code></p>}
      {uncertain && <div className="warning-callout"><CircleAlert size={18} /><p><strong>Действия приостановлены</strong><br />Результат запроса не подтверждён. Сначала проверьте журнал и не повторяйте операцию с новым ключом.</p></div>}
      {paperConfigured && !portfolio && <p className="empty-state">Портфель загружается или недоступен. Симуляции пока заблокированы.</p>}

      <button className="button button-secondary" disabled={busy || !paperConfigured}
        onClick={() => setRevision((value) => value + 1)}>
        <RefreshCw size={15} /> Обновить портфель
      </button>

      {portfolio && <>
        <div className="portfolio-summary">
          {([
            ['Виртуальные средства', portfolio.account.virtual_equity_usdt],
            ['Реализованный результат', portfolio.account.realized_pnl_usdt],
            ['Нереализованный результат', portfolio.account.unrealized_pnl_usdt],
            ['Результат за день', portfolio.performance.daily_pnl_usdt ?? null],
            ['Комиссии', portfolio.account.fees_paid_usdt],
            ['Проскальзывание', portfolio.account.slippage_cost_usdt],
          ] as [string, Json][]).map(([name, value]) => (
            <article className="portfolio-metric" key={name}>
              <span>{name}</span><strong>{display(value)}</strong><small>виртуальный USDT</small>
            </article>
          ))}
        </div>
        <div className={portfolio.reconciliation.status === 'error' ? 'reconciliation reconciliation-error' : 'reconciliation'}>
          <span>{portfolio.reconciliation.status === 'error' ? 'Нужна проверка учёта' : 'Учёт сверён'}</span>
          <small>{portfolio.positions.filter((position) => position.status === 'open').length} открытых позиций · {portfolio.orders.filter((order) => order.status === 'rejected').length} отклонённых симуляций</small>
        </div>
        {portfolio.account.status !== 'active' && <div className="warning-callout" role="alert">
          <CircleAlert size={18} /><p><strong>Счёт остановлен</strong><br />Найдено расхождение. Новые симуляции заблокированы до проверки учёта.</p>
        </div>}
        {Array.isArray(portfolio.account.unpriced_pnl_assets) && portfolio.account.unpriced_pnl_assets.length > 0 &&
          <p className="muted">Для части начального баланса неизвестна цена покупки, поэтому результат по этим активам не рассчитывается.</p>}
        <details className="advanced-panel">
          <summary>Виртуальные остатки и найденные расхождения <ChevronDown size={16} /></summary>
          <div className="table-wrap"><table><caption>Виртуальный баланс</caption><thead><tr><th>Актив</th><th>Доступно</th><th>Зарезервировано</th><th>Всего</th></tr></thead>
            <tbody>{portfolio.balances.map((balance) => <tr key={balance.asset}><td>{balance.asset}</td><td>{balance.available}</td><td>{balance.reserved}</td><td>{balance.total}</td></tr>)}</tbody>
          </table></div>
          <Records records={portfolio.reconciliation.issues} />
        </details>
      </>}

      <section className="residual-review">
        <div className="residual-heading"><div><p className="eyebrow">Контроль открытого остатка</p><h3>Совпадают ли объёмы двух сторон?</h3></div><Activity size={18} /></div>
        <p className="panel-lead">Если виртуально исполнены разные объёмы, система остановит новые симуляции. Автоматически закрывать такой остаток система не будет.</p>
        {residualError && <p role="alert" className="inline-alert">{residualError}</p>}
        {paperConfigured && !residuals && !residualError && <p className="muted">Проверяем сохранённые исполнения…</p>}
        {residuals?.length === 0 && <p className="empty-state">Сохранённых симуляций пока нет.</p>}
        {residuals?.map(({ execution_group_id, symbol, decision }) => (
          <article className={`residual-state residual-${decision.status}`} key={execution_group_id} role={decision.status === 'flat' ? 'status' : 'alert'}>
            <strong>{symbol} · {decision.status === 'flat' ? 'Объёмы совпадают' :
              decision.status === 'hedge_required' ? 'Остаток требует проверки' : 'Симуляция остановлена'}</strong>
            <p>{decision.human_reason}</p>
            <small>Причина: {decision.reason_code} · остаток {decision.residual_quantity} · оценка {decision.residual_notional_usd ?? 'нет свежей цены'} USDT</small>
            {decision.status !== 'flat' && <p className="muted">Не создавайте новые симуляции. Проверьте журнал и сверку учёта. Система не будет автоматически закрывать остаток.</p>}
          </article>
        ))}
      </section>

      <section className="preview-section">
        <div className="section-heading"><div><p className="eyebrow">Без обязательств</p><h3>Предварительный расчёт</h3></div></div>
        <p className="panel-lead">Сначала вы увидите расчёт. Сохранение виртуальной симуляции потребует отдельного подтверждения.</p>
        {!opportunities.length && <div className="empty-card"><span className="empty-icon"><Activity size={18} /></span><div><strong>Подходящих расчётов пока нет</strong><p>Дождитесь свежих котировок. Система не придумывает сигнал, если условия не выполнены.</p></div></div>}
        {opportunities.map((item) => {
          const blocked = simulationBlock(item, venues, now - receivedAt, maxAge);
          const disabled = !!blocked || busy || !portfolio || !residuals ||
            residuals.some((row) => row.decision.status !== 'flat') || uncertain ||
            portfolio.reconciliation.status === 'error' || portfolio.account.status !== 'active';
          return (
            <article className="paper-opportunity" key={item.id}>
              <div className="paper-opportunity-heading">
                <div><strong>{item.symbol}</strong><small>{venueLabel(item.buy_venue)} → {venueLabel(item.sell_venue)}</small></div>
                <span className={item.approved ? 'decision-tag decision-approved' : 'decision-tag decision-rejected'}>
                  {item.approved ? 'Условия пройдены' : 'Сделка отклонена'}
                </span>
              </div>
              <p>{item.summary}</p>
              <p className="plain-reason">{item.reason_text || item.reason}</p>
              <div className="net-callout"><span>Расчётная разница после расходов</span><strong>{item.expected_net_pct.toFixed(4)}%</strong></div>
              <button className="button button-primary" disabled={disabled} onClick={() => void showPreview(item)}>
                Посмотреть расчёт <ArrowRight size={16} />
              </button>
              {blocked && <small className="inline-hint">{blocked}</small>}
              <details className="advanced-panel">
                <summary>Подробные метрики и источники <ChevronDown size={16} /></summary>
                <div className="compact-metrics">
                  <span>Разница цен <strong>{item.gross_spread_pct}%</strong></span>
                  <span>Комиссии <strong>{item.fees_pct}%</strong></span>
                  <span>Проскальзывание <strong>{item.slippage_pct}%</strong></span>
                  <span>Возраст данных <strong>{Math.round(item.data_age_ms + Math.max(0, now - receivedAt))} мс</strong></span>
                  <span>Допустимый максимум <strong>{maxAge} мс</strong></span>
                </div>
                {venues.filter((venue) => [item.buy_venue, item.sell_venue].includes(venue.name)).map((venue) => (
                  <p key={venue.name} className="source-detail">{venueLabel(venue.name)} · {venue.status} · bid {venue.bid ?? 'нет данных'} / ask {venue.ask ?? 'нет данных'} · глубина {venue.depth_status}, {venue.bid_levels}/{venue.ask_levels} уровней</p>
                ))}
                {audit.filter((event) => event.opportunity_id === item.id).slice(0, 5).map((event) => (
                  <p key={event.id} className="source-detail">{event.timestamp} · {event.who ?? 'Система'} · {event.reason}</p>
                ))}
              </details>
            </article>
          );
        })}
      </section>

      <dialog className="confirm-dialog" ref={dialog} aria-labelledby="paper-confirm-title"
        onCancel={() => { if (!busy) setPreview(null); }}>
        {preview && <>
          <p className="eyebrow">Виртуальные средства</p>
          <h2 id="paper-confirm-title">Проверить симуляцию</h2>
          <div className="info-callout"><CircleAlert size={17} /><p><strong>Это не ордер на бирже.</strong><br />Сервер повторно проверит котировки и виртуальный баланс перед сохранением.</p></div>
          <p className="dialog-summary">{preview.item.symbol} · {venueLabel(preview.item.buy_venue)} → {venueLabel(preview.item.sell_venue)} · {preview.item.simulation_notional_usd} USDT</p>
          <p>{label(preview.result.order.status)} · {preview.result.order.human_reason}</p>
          <Records records={[preview.result.explanation]} />
          <div className="dialog-actions">
            <button className="button button-primary" disabled={busy || preview.result.order.status === 'rejected' || preview.result.reconciliation.status === 'error'}
              onClick={() => void mutate(preview.item)}>Сохранить paper-симуляцию</button>
            <button className="button button-secondary" disabled={busy} onClick={() => setPreview(null)}>Вернуться</button>
          </div>
        </>}
      </dialog>

      {result && <div className="result-card" role="status">
        <span className="decision-tag decision-approved">Симуляция сохранена</span>
        <h3>{label(result.order.status)}</h3><p>{result.order.human_reason}</p>
        <details className="advanced-panel"><summary>Результат сверки <ChevronDown size={16} /></summary><Records records={result.reconciliation.issues} /></details>
      </div>}

      {portfolio && <section className="saved-orders">
        <div className="section-heading"><div><p className="eyebrow">История счёта</p><h3>Сохранённые симуляции</h3></div></div>
        {!portfolio.orders.length ? <p className="empty-state">Здесь появятся сохранённые paper-симуляции.</p> :
          portfolio.orders.slice(0, 100).map((order) => (
            <article className="saved-order" key={order.id}>
              <div><strong>{order.symbol} · {label(order.status)}</strong><p>{order.human_reason}</p></div>
              <div className="saved-order-actions">
                <button className="button button-secondary" onClick={() => void getOrder(order.id).then(setDetail).catch(() => setMessage('Подробности недоступны.'))}>Подробности</button>
                {['created', 'accepted', 'partially_filled'].includes(order.status) &&
                  <button className="button button-secondary" disabled={busy || uncertain} onClick={() => void mutate(undefined, order.id)}>Отменить остаток</button>}
              </div>
            </article>
          ))}
        {detail && <details open className="advanced-panel"><summary>Подробности выбранной симуляции</summary><Records records={[detail]} /></details>}
        <details className="advanced-panel"><summary>Расширенный учёт: исполнения и позиции <ChevronDown size={16} /></summary>
          <h4>Виртуальные исполнения</h4><Records records={portfolio.fills} />
          <h4>Позиции</h4><Records records={portfolio.positions} />
          <h4>Показатели</h4><Records records={[portfolio.performance]} />
        </details>
        <AuditBrowser />
      </section>}
    </section>
  );
}
