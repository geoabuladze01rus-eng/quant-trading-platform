# Safety gates

Live trading is locked by default. Every connector `place_order` calls the central gate and is rejected unless all of these conditions hold:

- `LIVE_TRADING_ENABLED=true`;
- `TRADING_MODE=live`;
- `LIVE_ORDER_ACCEPTANCE_GATE=true`;
- T-Invest is not in sandbox mode.

Even if all gates were set, the MVP contains no real order execution and raises `NotImplementedError`. Risk decisions also reject stale data, API errors, balance mismatches, daily loss at or above 2%, excessive notional and insufficient net edge. Secrets are configuration-only, never returned by `/settings` or logged; `.env` is ignored by Git.

## Paper POST boundary

`POST /paper/orders/simulate` is the only POST route. It requires paper mode and
live disabled even if an acceptance flag is present. It cannot call connector
`place_order`, and it accepts no prices, credentials, fees, approval or mode overrides.
Malformed/extra request fields return 422. Business rejections return a paper
report with status `rejected`, a reason code/text and **no orders/fills**.

The engine independently checks current books, future/stale timestamps, source
identity, crypto/USD-quoted scope, finite positive size, per-trade limit, risk and
depth. It rejects last-good books after a venue error. Slippage/actual estimated
fees must leave sufficient net edge; a successful top-of-book decision is not
permission to bypass full-depth checks. No real exchange account is touched.

Risk score means `passed` or `blocked`, not probability. Existing daily-loss and
balance-mismatch risk checks remain tested, but this stage has no funded portfolio
to feed them: default zero daily loss/no mismatch are simulation assumptions, not
verified account health. Do not use this prototype to operate real funds.

The UI permits paper simulation only with an explicitly configured backend,
usable fresh public depth and paper approval. The backend rechecks every request.
Browser POST origins are limited to local Vite origins; CORS is not authentication.
Run locally, single worker; do not expose this unauthenticated API publicly.
Audit and paper history are bounded, volatile and not a compliance-grade journal.
