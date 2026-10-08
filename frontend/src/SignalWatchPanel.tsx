import { useEffect, useState } from 'react';
import { readSignalWatch, symbols, visibleAssets, visibleProviders } from './signalWatch';
import type { Calibration, WatchCandidate, WatchSnapshot } from './signalWatch';

const states: Record<string, string> = {
  ok: 'Свежие данные', stale: 'Данные устарели', error: 'Ошибка источника',
  no_data: 'Нет подтверждённых данных', disabled: 'Движок отключён',
  not_connected: 'Backend v4 не подключён',
};
function CandidateRows({ rows, historical = false }: { rows: WatchCandidate[]; historical?: boolean }) {
  return <div className="table-wrap"><table>
    <thead><tr><th>Актив / setup</th><th>Score / A–E</th><th>Оценка / причина</th><th>Время / режим</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.signal_id}>
      <td>{row.asset}<br />{row.setup}<details><summary>signal_id</summary><small>{row.signal_id}</small></details></td>
      <td>{row.score}<br /><small>{Object.entries(row.domains).map(([key, value]) => `${key}: ${value}`).join(' · ')}</small></td>
      <td>{row.confidence}<br /><small>{row.rejected_reason ?? (historical ? 'Оценка на момент записи' : 'Исследовательский кандидат')}</small>
        {row.missed_reason && <p>ANTI MISS: {row.missed_reason}</p>}</td>
      <td>{new Date(row.timestamp_ms).toLocaleString('ru-RU')}<br /><small>{row.market_regime}</small></td>
    </tr>)}</tbody>
  </table></div>;
}
function CalibrationView({ title, data }: { title: string; data: Record<string, Calibration> }) {
  return <article><h3>{title}</h3>{Object.keys(data).length === 0
    ? <p>Наблюдений пока нет.</p> : Object.entries(data).map(([label, bucket]) =>
      <p key={label}>{label}: выборка {bucket.samples}, положительных {bucket.positive},
        средняя net return {bucket.mean_net_return ?? 'нет данных'} (доля)</p>)}</article>;
}
export function SignalWatchView({ snapshot, elapsedMs, error }: {
  snapshot: WatchSnapshot | null; elapsedMs: number; error: string;
}) {
  const assets = snapshot ? visibleAssets(snapshot, elapsedMs) : {};
  return <section className="panel signal-watch" id="signal-watch">
    <div className="panel-header"><div><p className="eyebrow">Read-only paper research</p><h2>Crypto Signal Watch v4</h2></div>
      <span className="status-pill locked">LIVE OFF</span></div>
    <p>Исследовательские кандидаты. Реальные ордера не открываются, уведомления этим экраном не отправляются.</p>
    {error ? <p role="alert">{error}</p> : snapshot?.status
      ? <p role="status">{states[snapshot.status] ?? 'Состояние не подтверждено'}</p>
      : !snapshot ? <p role="status">Подключение v4…</p> : <>
        {symbols.map(symbol => <article className="watch-asset" key={symbol}>
          <h3>{symbol} · {states[assets[symbol]?.status] ?? 'Ожидание первого сканирования'}</h3>
          {assets[symbol]?.warnings.map(warning => <p className="muted" key={warning}>{warning}</p>)}
          {assets[symbol]?.candidates.length ? <CandidateRows rows={assets[symbol].candidates} />
            : <p>Текущие кандидаты отсутствуют или не прошли проверку свежести.</p>}
        </article>)}
        <details><summary>Источники подтверждений</summary>
          <p>Источник без данных не считается подтверждением. Повторный запрос не подключает отсутствующий источник.</p>
          <ul>{visibleProviders(snapshot, elapsedMs).map(row => <li key={`${row.source}-${row.symbol}`}>
            {row.source} · {row.symbol}: {states[row.status] ?? 'Состояние не подтверждено'}
          </li>)}</ul>
        </details>
        <details><summary>История кандидатов и ANTI MISS</summary>
          <p>Последние 100 записей журнала — не являются текущими сигналами. ANTI MISS фиксирует причины отклонения;
            прибыльность пропущенной возможности этим не доказана.</p>
          {snapshot.journal.rows.length ? <CandidateRows rows={snapshot.journal.rows} historical /> : <p>Журнал пока пуст.</p>}
        </details>
        <details><summary>Калибровка</summary>
          <p>Расчётный markout через 15 минут учитывает оценочные комиссии и проскальзывание;
            это не результат исполнения ордера. Наблюдённые результаты поступают отдельно.</p>
          <CalibrationView title="Расчётный markout" data={snapshot.outcomes.calibration} />
          <CalibrationView title="Наблюдённые результаты" data={snapshot.observed_outcomes.calibration} />
        </details>
      </>}
  </section>;
}
export function SignalWatchPanel({ baseUrl }: { baseUrl?: string }) {
  const [snapshot, setSnapshot] = useState<WatchSnapshot | null>(null);
  const [error, setError] = useState('');
  const [elapsedMs, setElapsedMs] = useState(0);
  useEffect(() => {
    let disposed = false, received = performance.now();
    let timer: ReturnType<typeof setTimeout>;
    const tick = setInterval(() => setElapsedMs(Math.max(0, Math.floor(performance.now() - received))), 250);
    async function load() {
      try {
        const result = await readSignalWatch(baseUrl);
        if (disposed) return;
        received = performance.now();
        setElapsedMs(0);
        setSnapshot(result); setError('');
      } catch (cause) {
        if (disposed) return;
        setSnapshot(null);
        setError(cause instanceof Error ? cause.message : 'Данные v4 недоступны');
      } finally {
        if (!disposed && baseUrl) timer = setTimeout(() => void load(), 5000);
      }
    }
    void load();
    return () => { disposed = true; clearTimeout(timer); clearInterval(tick); };
  }, [baseUrl]);
  return <SignalWatchView snapshot={snapshot} elapsedMs={elapsedMs} error={error} />;
}
