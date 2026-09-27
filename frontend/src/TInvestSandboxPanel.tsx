import { useEffect, useState } from 'react';

interface JsonRecord {
  [key: string]: unknown;
}

interface SandboxStatus {
  configured: boolean;
  ready: boolean;
  account_configured: boolean;
  orders_enabled: boolean;
  order_submission_available: boolean;
  live_trading: string;
  live_execution: boolean;
  max_order_lots: number;
}

interface SandboxAccount {
  account_id: string;
  status: string;
}

interface AccountData {
  portfolio: JsonRecord;
  positions: JsonRecord;
  orders: JsonRecord;
}

const apiBase = import.meta.env.VITE_API_BASE_URL?.trim().replace(/\/+$/, '');

function object(value: unknown): JsonRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Сервер вернул данные в неизвестном формате.');
  }
  return value as JsonRecord;
}

async function readApi(path: string): Promise<JsonRecord> {
  if (!apiBase) {
    throw new Error('Сначала подключите локальный сервер приложения.');
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 7000);
  try {
    const response = await fetch(`${apiBase}${path}`, {
      method: 'GET',
      credentials: 'omit',
      cache: 'no-store',
      signal: controller.signal,
    });
    if (!response.ok) {
      if (response.status === 503) {
        throw new Error('T‑Инвестиции пока не настроены. Добавьте sandbox-токен в локальный .env и перезапустите сервер.');
      }
      throw new Error('Не удалось получить данные демо-счёта. Проверьте соединение и повторите запрос.');
    }
    return object(await response.json());
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('T‑Инвестиции')) throw error;
    throw new Error('Сервер недоступен или вернул некорректные данные. Счёт не обновлён.');
  } finally {
    clearTimeout(timeout);
  }
}

async function readStatus(): Promise<SandboxStatus> {
  const value = await readApi('/t-invest/sandbox/status');
  if (
    typeof value.configured !== 'boolean' ||
    typeof value.ready !== 'boolean' ||
    typeof value.account_configured !== 'boolean' ||
    typeof value.orders_enabled !== 'boolean' ||
    typeof value.order_submission_available !== 'boolean' ||
    typeof value.live_trading !== 'string' ||
    typeof value.live_execution !== 'boolean' ||
    typeof value.max_order_lots !== 'number'
  ) {
    throw new Error('Сервер вернул некорректное состояние sandbox.');
  }
  return value as unknown as SandboxStatus;
}

async function readAccounts(): Promise<SandboxAccount[]> {
  const value = await readApi('/t-invest/sandbox/accounts');
  if (!Array.isArray(value.accounts)) throw new Error('Сервер вернул некорректный список счетов.');
  return value.accounts.map((item: unknown) => {
    const account = object(item);
    if (typeof account.account_id !== 'string' || typeof account.status !== 'string') {
      throw new Error('Сервер вернул некорректные данные счёта.');
    }
    return { account_id: account.account_id, status: account.status };
  });
}

async function readAccount(accountId: string): Promise<AccountData> {
  const path = `/t-invest/sandbox/accounts/${encodeURIComponent(accountId)}`;
  const [portfolioResponse, positionsResponse, ordersResponse] = await Promise.all([
    readApi(`${path}/portfolio`),
    readApi(`${path}/positions`),
    readApi(`${path}/orders`),
  ]);
  return {
    portfolio: object(portfolioResponse.portfolio),
    positions: object(positionsResponse.positions),
    orders: object(ordersResponse.orders),
  };
}

function moneyValue(value: unknown): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return 'Нет данных';
  const money = value as JsonRecord;
  if (money.units === undefined || money.currency === undefined) return 'Нет данных';
  const nano = String(money.nano ?? 0).replace('-', '').padStart(9, '0').replace(/0+$/, '');
  const units = String(money.units);
  const currency = String(money.currency).toUpperCase();
  return `${units}${nano ? `.${nano}` : ''} ${currency}`;
}

function countItems(value: unknown): number | null {
  return Array.isArray(value) ? value.length : null;
}

export function TInvestSandboxPanel() {
  const [status, setStatus] = useState<SandboxStatus | null>(null);
  const [accounts, setAccounts] = useState<SandboxAccount[]>([]);
  const [selectedAccount, setSelectedAccount] = useState('');
  const [data, setData] = useState<AccountData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    void readStatus()
      .then((value) => { if (active) setStatus(value); })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'Состояние sandbox неизвестно.');
      });
    return () => { active = false; };
  }, []);

  async function loadAccounts() {
    setLoading(true);
    setError('');
    setAccounts([]);
    setSelectedAccount('');
    setData(null);
    try {
      const nextStatus = await readStatus();
      setStatus(nextStatus);
      if (!nextStatus.ready) {
        throw new Error('Подключение заблокировано: проверьте sandbox-токен, paper-режим и блокировку реальной торговли.');
      }
      const nextAccounts = await readAccounts();
      setAccounts(nextAccounts);
      if (nextAccounts.length === 1) setSelectedAccount(nextAccounts[0].account_id);
      if (nextAccounts.length === 0) {
        setError('Sandbox-счет не найден. Создайте его в кабинете T‑Инвестиции и повторите загрузку.');
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось загрузить демо-счета.');
    } finally {
      setLoading(false);
    }
  }

  async function loadAccount() {
    if (!selectedAccount) return;
    setLoading(true);
    setError('');
    setData(null);
    try {
      setData(await readAccount(selectedAccount));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось обновить демо-счёт.');
    } finally {
      setLoading(false);
    }
  }

  const securities = data ? countItems(data.positions.securities) : null;
  const activeOrders = data ? countItems(data.orders.orders) : null;
  const portfolioValue = data?.portfolio.totalAmountPortfolio;

  return (
    <section className="panel tinvest-panel" id="t-invest-sandbox" aria-labelledby="tinvest-title">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Отдельный тестовый контур</p>
          <h2 id="tinvest-title">Демо-счёт Т‑Инвестиции</h2>
        </div>
        <span className="tinvest-lock">Реальные сделки выключены</span>
      </div>
      <p className="panel-lead">
        Здесь будут показаны только виртуальный портфель, позиции и заявки sandbox.
        Этот экран пока работает в режиме просмотра.
      </p>

      <div className="tinvest-safety" role="status">
        <strong>Песочница · реальные деньги не затрагиваются</strong>
        <span>
          {!apiBase
            ? 'Подключите локальный сервер приложения.'
            : status === null
              ? 'Проверяем безопасное состояние приложения…'
              : status.ready
                ? 'Режим paper подтверждён. Отправка заявок из платформы недоступна.'
                : status.configured
                  ? 'Токен задан, но защитные условия подключения не выполнены.'
                  : 'Sandbox-токен ещё не настроен.'}
        </span>
      </div>

      {!status?.configured && (
        <p className="tinvest-help">
          Чтобы подключиться, сохраните sandbox-токен только в локальном файле .env
          как T_INVEST_API_TOKEN и перезапустите сервер. Не вставляйте токен в интерфейс,
          чат или репозиторий.
        </p>
      )}

      <div className="tinvest-actions">
        <button className="button button-secondary" type="button"
          onClick={() => void loadAccounts()} disabled={loading || !status?.ready}>
          {loading ? 'Загружаем…' : 'Загрузить демо-счёт'}
        </button>
        {accounts.length > 1 && (
          <label className="tinvest-select">
            Выберите тестовый счёт
            <select value={selectedAccount} onChange={(event) => {
              setSelectedAccount(event.target.value);
              setData(null);
            }}>
              <option value="">Выберите счёт</option>
              {accounts.map((account) => (
                <option key={account.account_id} value={account.account_id}>
                  {account.account_id} · {account.status}
                </option>
              ))}
            </select>
          </label>
        )}
        {selectedAccount && accounts.length > 1 && (
          <button className="button button-primary" type="button"
            onClick={() => void loadAccount()} disabled={loading}>
            Открыть портфель
          </button>
        )}
      </div>

      {error && <p className="inline-alert" role="alert">{error}</p>}

      {accounts.length === 1 && !data && (
        <div className="tinvest-account-choice">
          <span>Тестовый счёт найден: <strong>{accounts[0].account_id}</strong></span>
          <button className="button button-primary" type="button"
            onClick={() => void loadAccount()} disabled={loading}>
            {loading ? 'Загружаем портфель…' : 'Открыть портфель'}
          </button>
        </div>
      )}

      {data && (
        <>
          <div className="tinvest-grid">
            <article><span>Счёт</span><strong>{selectedAccount}</strong></article>
            <article><span>Стоимость портфеля</span><strong>{moneyValue(portfolioValue)}</strong></article>
            <article><span>Позиции</span><strong>{securities ?? 'Нет данных'}</strong></article>
            <article><span>Заявки</span><strong>{activeOrders ?? 'Нет данных'}</strong></article>
          </div>
          <details className="tinvest-details">
            <summary>Показать технические данные портфеля и позиций</summary>
            <pre>{JSON.stringify({ portfolio: data.portfolio, positions: data.positions, orders: data.orders }, null, 2)}</pre>
          </details>
        </>
      )}
      <p className="tinvest-footnote">
        Отправка и отмена заявок из платформы выключены. Для операций используйте
        непосредственно терминал Т‑Инвестиции; в этой сборке нет управляющих кнопок.
      </p>
    </section>
  );
}
