import {
  Activity, ArrowRight, BarChart3, Check, ChevronDown,
  CircleHelp, Database, FileText, Gauge, LockKeyhole, RefreshCw, Settings,
  ShieldCheck, WalletCards,
} from 'lucide-react';
import { useEffect, useState } from 'react';

import { getAudit, getOpportunities, getRisk, getSettings, getVenues, isMockMode } from './apiClient';
import type { AuditEvent, DashboardSettings, OpportunityResponse, Risk, Venue } from './types';
import { PaperAlpha as PaperExecution } from './PaperAlpha';

const money = new Intl.NumberFormat('ru-RU', {
  style: 'currency', currency: 'USD', maximumFractionDigits: 0,
});
const pct = (value: number) => `${value.toFixed(2)}%`;
const market = (value: string) => ({
  crypto: 'Крипторынок', russian_stocks: 'Российский рынок', mixed: 'Смешанный рынок',
}[value] ?? value);
const venueName = (value: string) => ({
  binance: 'Binance', bybit: 'Bybit', okx: 'OKX', t_invest: 'Т‑Инвестиции',
}[value] ?? value);
const sourceStatus = {
  ok: 'Данные получены', no_data: 'Ожидаем данные', stale: 'Данные устарели',
  error: 'Ошибка источника', disabled: 'Отключён',
};
const sourceMode = {
  public_read_only: 'Публичные данные · только чтение',
  sandbox: 'Песочница · только чтение',
  disabled: 'Источник отключён',
};

function MarketSource({ venue }: { venue: Venue }) {
  const hasQuote = venue.bid !== null && venue.ask !== null;
  const unavailable = venue.status !== 'ok';
  const explanation = {
    ok: 'Источник отвечает. Котировки используются только для расчётов.',
    no_data: venue.mode === 'sandbox'
      ? 'Песочница не подключена. Портфель и котировки не получены.'
      : 'Ждём корректный биржевой стакан.',
    stale: 'Котировка устарела и исключена из расчёта возможностей.',
    error: 'Не удалось проверить источник. Его котировки не используются.',
    disabled: 'Получение данных для этого источника отключено.',
  }[venue.status];

  return (
    <article className={`source-card source-${venue.status}`}>
      <div className="source-heading">
        <strong>{venueName(venue.name)}</strong>
        <span className={`source-status source-status-${venue.status}`}>
          <i aria-hidden="true" />{sourceStatus[venue.status]}
        </span>
      </div>
      <small>{market(venue.market)} · {isMockMode ? 'Демонстрационные данные' : sourceMode[venue.mode]}</small>
      <p className="source-explanation">{explanation}</p>
      {hasQuote && (
        <div className={unavailable ? 'source-quote muted' : 'source-quote'}>
          <span>{venue.symbol} {unavailable ? '· последняя котировка, только для справки' : '· актуально на момент обновления'}</span>
          <span>Покупка {venue.ask} · Продажа {venue.bid}</span>
        </div>
      )}
      <small>Возраст данных: {venue.data_age_ms === null ? 'неизвестен' : `${venue.data_age_ms} мс`}</small>
      {venue.timestamp_source && (
        <small>Время: {venue.timestamp_source === 'exchange' ? 'биржевое' :
          venue.timestamp_source === 'request_start' ? 'начало запроса' :
          venue.timestamp_source === 'receipt' ? 'получение ответа' : 'источник неизвестен'}</small>
      )}
    </article>
  );
}

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
  const [receivedAt, setReceivedAt] = useState(0);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      const requestedAt = Date.now();
      try {
        const [settings, venues, opportunities, risk, audit] = await Promise.all([
          getSettings(), getVenues(), getOpportunities(), getRisk(), getAudit(),
        ]);
        if (disposed) return;
        if (settings.live_trading_enabled || settings.trading_mode === 'live' ||
            !settings.t_invest_sandbox || !risk.live_trading_locked ||
            venues.some((venue) => venue.live_execution)) {
          throw new Error('Не удалось подтвердить безопасный paper-режим. Действия заблокированы.');
        }
        setData({ settings, venues, opportunities, risk, audit });
        setReceivedAt(requestedAt);
        setError('');
        setUpdatedAt(new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }));
      } catch (cause) {
        if (disposed) return;
        setData(null);
        setError(cause instanceof Error ? cause.message : 'Сервер недоступен. Данные не проверены.');
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
        <a className="brand" href="#overview" aria-label="Quant Platform — главная">
          <span className="brand-mark">Q</span>
          <span className="brand-copy"><strong>Quant Platform</strong><small>Понятная paper-торговля</small></span>
        </a>
        <div className="sidebar-mode"><span className="mode-dot" /> Учебный режим · без денег</div>
        <nav className="nav-list" aria-label="Главная навигация">
          <a className="nav-item active" href="#overview"><Gauge size={18} /> Обзор</a>
          <a className="nav-item" href="#sources"><Database size={18} /> Рыночные данные</a>
          <a className="nav-item" href="#opportunities"><BarChart3 size={18} /> Возможности</a>
          <a className="nav-item" href="#paper"><WalletCards size={18} /> Paper-портфель</a>
          <a className="nav-item" href="#risk"><ShieldCheck size={18} /> Защита и риск</a>
          <a className="nav-item" href="#audit"><FileText size={18} /> Журнал решений</a>
          <a className="nav-item" href="#settings"><Settings size={18} /> Настройки</a>
        </nav>
        <div className="sidebar-foot">
          <LockKeyhole size={16} />
          <span><strong>Реальные сделки выключены</strong><small>Биржевые ключи не подключены</small></span>
        </div>
      </aside>

      <main className="main-content" id="overview">
        <header className="topbar">
          <div className="topbar-heading">
            <p className="eyebrow">Ваш учебный торговый кабинет</p>
            <h1>Добро пожаловать в Paper Alpha</h1>
            <p className="page-intro">Здесь можно изучать рыночные данные и проверять расчёты без реальных денег.</p>
          </div>
          <button className="button button-secondary refresh-button" type="button"
            onClick={() => setRefresh((value) => value + 1)}>
            <RefreshCw size={16} /> Обновить данные
          </button>
        </header>

        <div className="safety-banner" role={error ? 'alert' : 'status'}>
          <div className="safety-icon"><LockKeyhole size={18} /></div>
          <div className="safety-copy">
            <strong>{error ? 'Данные не подтверждены' : isMockMode ? 'Демонстрационный режим' : data ? 'Paper-режим активен' : 'Подключаемся к серверу'}</strong>
            <p>{error || (isMockMode
              ? 'Показаны учебные примеры. Биржи не подключены, действия с портфелем недоступны.'
              : data
                ? `Используются публичные котировки. Последнее обновление: ${updatedAt || 'ожидается'}.`
                : 'Проверяем соединение и защитные настройки.')}</p>
          </div>
          <span className="live-lock"><LockKeyhole size={14} /> Реальные ордера выключены</span>
        </div>

        <section className="getting-started" aria-label="Как пользоваться кабинетом">
          <div className="section-heading">
            <div><p className="eyebrow">Начните здесь</p><h2>Три шага, чтобы разобраться</h2></div>
            <span className="beginner-badge"><CircleHelp size={15} /> Не нужен опыт в трейдинге</span>
          </div>
          <div className="step-grid">
            <a className="step-card" href="#sources"><span className="step-number">01</span><span><strong>Проверьте данные</strong><small>Убедитесь, что источники отвечают и котировки свежие.</small></span><ArrowRight size={17} /></a>
            <a className="step-card" href="#opportunities"><span className="step-number">02</span><span><strong>Изучите расчёт</strong><small>Посмотрите комиссии, проскальзывание и итоговую разницу цен.</small></span><ArrowRight size={17} /></a>
            <a className="step-card" href="#paper"><span className="step-number">03</span><span><strong>Откройте симуляцию</strong><small>Проверьте, как выглядела бы сделка в виртуальном портфеле.</small></span><ArrowRight size={17} /></a>
          </div>
        </section>

        <section className="metric-grid" aria-label="Основные ограничения">
          <article className="metric-card metric-card-accent"><span>Минимальная выгода после расходов</span><strong>{data ? pct(data.risk.min_expected_net_pct) : '—'}</strong><small>Комиссии и проскальзывание уже учитываются</small></article>
          <article className="metric-card"><span>Лимит виртуальной сделки</span><strong>{data ? money.format(data.risk.max_trade_notional_usd) : '—'}</strong><small>Ограничение paper-симуляции</small></article>
          <article className="metric-card"><span>Максимальная дневная просадка</span><strong>{data ? pct(data.risk.max_daily_loss_pct) : '—'}</strong><small>При превышении риск-система блокирует действие</small></article>
          <article className="metric-card metric-card-lock"><span>Реальная торговля</span><strong><LockKeyhole size={17} /> Заблокирована</strong><small>В этой версии нельзя отправить ордер на биржу</small></article>
        </section>

        <section className="content-grid">
          <article className="panel" id="sources">
            <div className="panel-header">
              <div><p className="eyebrow">Сначала проверьте</p><h2>Состояние источников</h2></div>
              <span className="icon-tile"><Database size={18} /></span>
            </div>
            <p className="panel-lead">Устаревшие данные не используются для расчёта возможности.</p>
            <div className="health-list">
              {data?.venues.map((venue) => <MarketSource venue={venue} key={venue.name} />)}
              {!data && <p className="empty-state">Состояние бирж пока неизвестно. Не принимайте решения по этим данным.</p>}
            </div>
          </article>

          <article className="panel">
            <div className="panel-header">
              <div><p className="eyebrow">Прозрачность решений</p><h2>Что делает система</h2></div>
              <span className="icon-tile"><Activity size={18} /></span>
            </div>
            <p className="panel-lead">Каждый результат можно объяснить и проверить.</p>
            <div className="principle-list">
              <div><span className="principle-icon"><Check size={15} /></span><p><strong>Считает итоговую разницу</strong><small>Вычитает комиссии и ожидаемое проскальзывание.</small></p></div>
              <div><span className="principle-icon"><Check size={15} /></span><p><strong>Проверяет ограничения</strong><small>Свежесть данных, размер сделки и виртуальный баланс.</small></p></div>
              <div><span className="principle-icon"><Check size={15} /></span><p><strong>Объясняет отказ</strong><small>Показывает причину, если условие не выполнено.</small></p></div>
              <div><span className="principle-icon principle-icon-lock"><LockKeyhole size={14} /></span><p><strong>Не торгует реальными средствами</strong><small>Биржи доступны только для получения публичных котировок.</small></p></div>
            </div>
          </article>
        </section>

        <section className="panel opportunity-panel" id="opportunities">
          <div className="panel-header">
            <div><p className="eyebrow">Без обещаний доходности</p><h2>Возможности рынка</h2></div>
            <span className="icon-tile"><BarChart3 size={18} /></span>
          </div>
          <p className="panel-lead">Система сравнивает цену покупки и продажи, затем вычитает расходы. Положительное число — расчётная разница, а не гарантированная прибыль.</p>
          {!rows.length
            ? <div className="empty-card"><span className="empty-icon"><Activity size={18} /></span><div><strong>{data ? 'Подходящих возможностей сейчас нет' : 'Данные пока не загружены'}</strong><p>{data ? 'Это нормально. Система не должна придумывать сигнал, если условия не выполнены.' : 'Сначала дождитесь подтверждённого соединения.'}</p></div></div>
            : <div className="opportunity-list">{rows.map((item, index) => (
              <article className="opportunity-card" key={`${item.strategy}-${item.symbol}-${item.buy_venue}-${item.sell_venue}-${index}`}>
                <div className="opportunity-main">
                  <div className="opportunity-route">
                    <span className="symbol-badge">{item.symbol}</span>
                    <span>{venueName(item.buy_venue)}</span><ArrowRight size={15} /><span>{venueName(item.sell_venue)}</span>
                  </div>
                  <p>{item.summary}</p>
                  <small>Котировка обновлена ${item.data_age_ms} мс назад</small>
                </div>
                <div className="opportunity-result">
                  <span className={`decision-tag ${item.approved ? 'decision-approved' : 'decision-rejected'}`}>
                    {item.approved ? 'Можно проверить в симуляции' : 'Сделка отклонена'}
                  </span>
                  <strong className={item.expected_net_pct > 0 ? 'net-positive' : 'net-neutral'}>{pct(item.expected_net_pct)}</strong>
                  <small>расчётная разница после расходов</small>
                </div>
                <details className="opportunity-details">
                  <summary>Почему такой результат? <ChevronDown size={15} /></summary>
                  <p className="plain-reason">{item.reason_text || item.reason}</p>
                  <div className="compact-metrics">
                    <span>Разница цен <strong>{pct(item.gross_spread_pct)}</strong></span>
                    <span>Комиссии <strong>−{pct(item.fees_pct)}</strong></span>
                    <span>Проскальзывание <strong>−{pct(item.slippage_pct)}</strong></span>
                    <span>Максимальный размер <strong>{money.format(item.max_notional_usd)}</strong></span>
                    <span>Проверка риска <strong>{item.approved ? 'Пройдена' : 'Не пройдена'}</strong></span>
                  </div>
                  <small>Расчётный размер для симуляции: {item.simulation_notional_usd} USDT</small>
                </details>
              </article>
            ))}</div>}
        </section>

        {data && <PaperExecution opportunities={rows} venues={data.venues} audit={data.audit} receivedAt={receivedAt} maxAge={data.settings.max_market_data_age_ms} />}

        <section className="content-grid" id="risk">
          <article className="panel">
            <div className="panel-header">
              <div><p className="eyebrow">Защитные правила</p><h2>Как система ограничивает риск</h2></div>
              <span className="icon-tile"><ShieldCheck size={18} /></span>
            </div>
            {data ? <ul className="protection-list">
              <li><Check size={16} /> Устаревшие данные блокируют расчёт <strong>{data.risk.stale_data_protection ? 'Включено' : 'Не подтверждено'}</strong></li>
              <li><Check size={16} /> Ошибка источника блокирует расчёт <strong>{data.risk.api_error_protection ? 'Включено' : 'Не подтверждено'}</strong></li>
              <li><Check size={16} /> Несовпадение баланса блокирует сделки <strong>{data.risk.balance_mismatch_protection ? 'Включено' : 'Не подтверждено'}</strong></li>
              <li><LockKeyhole size={16} /> Отправка реальных ордеров <strong>Заблокирована</strong></li>
            </ul> : <p className="empty-state">Настройки безопасности не загружены. Состояние не подтверждено.</p>}
          </article>

          <article className="panel" id="settings">
            <div className="panel-header">
              <div><p className="eyebrow">Только просмотр</p><h2>Режим и подключения</h2></div>
              <span className="icon-tile"><Settings size={18} /></span>
            </div>
            <div className="settings-grid">
              <label>Режим платформы<input value={data?.settings.trading_mode === 'paper' ? 'Paper — симуляция' : data?.settings.trading_mode ?? 'Неизвестно'} readOnly /></label>
              <label>Рынок<input value={data ? market(data.settings.market_scope) : 'Не проверен'} readOnly /></label>
              <label>Т‑Инвестиции<input value={data?.settings.t_invest_sandbox ? 'Только sandbox' : 'Не подтверждено'} readOnly /></label>
              <label>Реальные ордера<input value="Заблокированы" readOnly /></label>
            </div>
            <p className="settings-note">Секретные ключи не запрашиваются и не показываются в интерфейсе.</p>
          </article>
        </section>

        <section className="panel" id="audit">
          <div className="panel-header">
            <div><p className="eyebrow">История решений</p><h2>Журнал действий</h2></div>
            <span className="icon-tile"><FileText size={18} /></span>
          </div>
          <p className="panel-lead">Журнал помогает понять, что произошло и почему система приняла такое решение.</p>
          {!data?.audit.length
            ? <div className="empty-card"><span className="empty-icon"><FileText size={18} /></span><div><strong>{data ? 'Записей пока нет' : 'Журнал недоступен'}</strong><p>Новые решения появятся после получения данных.</p></div></div>
            : <div className="table-wrap"><table><thead><tr><th>Время</th><th>Рынок</th><th>Кто</th><th>Событие</th><th>Стратегия</th><th>Причина</th></tr></thead>
              <tbody>{data.audit.slice(0, 100).map((event) => <tr key={event.id}>
                <td>{event.timestamp}</td><td>{market(event.market_scope)}</td><td>{event.who ?? 'Система'}</td>
                <td>{event.event} · {event.decision ?? 'решение'}</td><td>{event.strategy}</td><td className="reason-cell">{event.reason}</td>
              </tr>)}</tbody></table></div>}
        </section>
        <footer className="app-footer"><span>Quant Platform · Paper Alpha</span><span><LockKeyhole size={13} /> Только симуляция. Не финансовая рекомендация.</span></footer>
      </main>
    </div>
  );
}
