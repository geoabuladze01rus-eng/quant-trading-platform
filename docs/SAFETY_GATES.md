# Safety gates

Live trading is locked by default. Every connector `place_order` calls the central gate and is rejected unless all of these conditions hold:

- `LIVE_TRADING_ENABLED=true`;
- `TRADING_MODE=live`;
- `LIVE_ORDER_ACCEPTANCE_GATE=true`;
- T-Invest is not in sandbox mode.

Even if all gates were set, the MVP contains no real order execution and raises `NotImplementedError`. Risk decisions also reject stale data, API errors, balance mismatches, daily loss at or above 2%, excessive notional and insufficient net edge. Secrets are configuration-only, never returned by `/settings` or logged; `.env` is ignored by Git.
