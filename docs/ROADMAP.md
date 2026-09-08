# Roadmap

1. **MVP foundation (delivered):** safety gates, detectors, dashboard and full regression CI.
2. **Public data (delivered):** keyless Binance/Bybit/OKX books, normalization and
   freshness/source health; T-Invest sandbox read transport boundary only.
3. **Explainable paper execution (current):** all-or-reject depth fills, actual
   estimated fees, slippage reserve, expected/simulated reconciliation and audit.
   This stage does not implement funded inventory or independent partial leg fills.
4. **Durable paper accounting (next):** persisted event journal, request idempotency,
   explicit funded per-venue balances/reservations, daily P&L and balance reconciliation
   as actual execution gates, restart/replay tests and longer public-feed observation.
5. **Execution lifecycle:** stateful partial fills, residual exposure and simulated
   protective hedging, halt/recovery; extend the canonical paper engine rather than
   introducing another execution architecture.
6. **Controlled integration:** validate T-Invest sandbox reads with a separate
   least-privilege setup. Future live trading requires a separate explicit acceptance
   process and security review; this roadmap does not enable or authorize it.
