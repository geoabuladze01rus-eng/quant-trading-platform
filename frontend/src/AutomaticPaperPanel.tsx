import { useEffect, useState } from 'react';
import { getAutomaticPaper, paperConfigured } from './paperApi';
import type { AutoPaperSnapshot, Json } from './paperApi';

const show = (value: Json | undefined) => value === undefined || value === null ? 'Нет данных' : String(value);

export function AutomaticPaperPanel() {
  const [snapshot, setSnapshot] = useState<AutoPaperSnapshot | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!paperConfigured) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const value = await getAutomaticPaper();
        if (active) { setSnapshot(value); setError(''); }
      } catch {
        if (active) { setSnapshot(null); setError('Состояние робота недоступно. Торговля и баланс не подтверждены.'); }
      } finally {
        if (active) timer = setTimeout(() => void refresh(), 5000);
      }
    };
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, []);
  const robot = snapshot?.robot;
  const heartbeatOld = typeof robot?.heartbeat_age_ms === 'number' && robot.heartbeat_age_ms > 15000;
  return <section aria-label="Automatic OKX paper trading">
    <h3>OKX · автоматическая учебная торговля</h3>
    <p>Отдельный виртуальный счёт в USDT. BTC/USDT, ETH/USDT и LTC/USDT · дневной тренд.</p>
    {!paperConfigured && <p>Подключите сервер терминала для проверки исполнения.</p>}
    {error && <p role="alert">{error}</p>}
    {paperConfigured && !snapshot && !error && <p>Состояние загружается…</p>}
    {snapshot && robot && <>
      <p role="status">{show(robot.status)} · {show(robot.human_reason)}</p>
      {(!robot.worker_running || heartbeatOld) && <p role="alert">Работающий цикл не подтверждён: проверьте запуск и время последней проверки.</p>}
      <p>Заявок: {show(robot.orders_count)} · Исполнений: {show(robot.fills_count)} · Сверка: {snapshot.reconciliation.status}</p>
      <p>Последняя проверка: {typeof robot.checked_at_ms === 'number' ? new Date(robot.checked_at_ms).toLocaleString('ru-RU') : 'Нет данных'} · Возраст оценки: {show(robot.valuation_age_ms)} мс</p>
      <p>Последняя оценка капитала: {show(robot.equity_usdt)} USDT · Реализованный результат: {snapshot.account.realized_pnl_usdt} USDT · Комиссии: {snapshot.account.fees_paid_usdt} USDT</p>
      <div className="table-wrap"><table><caption>Последние сохранённые виртуальные заявки</caption>
        <thead><tr><th>Пара</th><th>Направление</th><th>Статус</th><th>Объём, USDT</th></tr></thead>
        <tbody>{snapshot.orders.map((order) => <tr key={show(order.order_id)}><td>{show(order.symbol)}</td><td>{show(order.side)}</td><td>{show(order.status)}</td><td>{show(order.requested_notional_usd)}</td></tr>)}</tbody>
      </table></div>
      {!snapshot.orders.length && <p>Сохранённых заявок пока нет. Причины по каждой паре показаны ниже.</p>}
      {Array.isArray(robot.decisions) && robot.decisions.map((item, index) => {
        if (!item || typeof item !== 'object' || Array.isArray(item)) return null;
        return <p key={index}>{show(item.symbol)} · {show(item.signal_human_reason)} · {show(item.human_reason)} · Издержки полного цикла, оценка: {show(item.estimated_round_trip_cost_pct)}%</p>;
      })}
    </>}
  </section>;
}
