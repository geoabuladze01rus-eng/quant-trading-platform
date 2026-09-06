# Frontend

Risk-first dashboard for the Quant Trading Platform.

## Current scope

Set `VITE_API_BASE_URL=http://localhost:8000` to read backend data. The dashboard
polls every 10 seconds; source status and quote age are explicitly labelled as
observed at refresh. Binance, Bybit and OKX use public read-only market data.
T-Invest is separately labelled sandbox/read-only and shows no data until connected.

Each source distinguishes healthy, no data, stale, error and disabled states.
Last good quotes retained by the backend during stale/error states are reference
only and excluded from opportunities. Upstream error payloads are never rendered.
An unavailable or invalid backend response clears the dashboard's prior snapshot.

Without `VITE_API_BASE_URL`, the dashboard displays explicitly labelled demo/mock
fixtures. Fixtures never replace a failed backend request or generate fills.
The frontend only submits paper simulation requests; live execution remains locked.

The explainable feed uses `/opportunities?explain=true`. Each item exposes its
summary, decision code/text, deterministic risk gate score, costs and net edge.
Advanced details show venue quotes, timestamp source, depth and decision audit.
The explicit **Simulate paper order** action sends symbol, buy/sell venues and
the server-provided simulation notional to `POST /paper/orders/simulate`.
It requires approval, healthy public depth, a configured backend and a loaded
ledger. Quote ages advance locally from the request start; controls expire at
`max_market_data_age_ms` (default 1000 ms). The backend rechecks risk and books.
`GET /paper/orders`, `/paper/fills`, and `/reconciliation` show the paper ledger.
An uncertain POST response is never retried automatically; inspect the ledger.

Screens included:

- Command center
- Opportunities
- Risk center
- Audit log
- Settings preview

## Run locally

```bash
cd frontend
npm ci
npm run dev
```

Open the Vite URL shown in the terminal.

## Build

```bash
npm run build
```

## Design rules

- Risk state is more important than profit.
- No manual buy/sell buttons in MVP.
- No API secrets in the UI.
- Green means confirmed positive net edge only.
- Red means loss, rejection, blocked state, or danger.
- Amber means warning or locked state.
