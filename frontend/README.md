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
fixtures. Fixtures never replace a failed backend request. The frontend does not
send orders or accept API secrets; live execution remains locked.

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
