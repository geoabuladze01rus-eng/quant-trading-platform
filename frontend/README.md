# Frontend

Risk-first dashboard for the Quant Trading Platform.

## Current scope

The frontend uses mock data and does not send real orders. Live trading controls are intentionally locked in the MVP.

Screens included:

- Command center
- Opportunities
- Risk center
- Audit log
- Settings preview

## Run locally

```bash
cd frontend
npm install
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
