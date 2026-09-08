# Roadmap

1. **MVP foundation — delivered:** safety gates, detectors, dashboard and regression CI.
2. **Public data — delivered:** keyless Binance/Bybit/OKX books, normalization,
   freshness/source health, and a T-Invest sandbox/read boundary.
3. **Explainable depth simulation — delivered:** fees, slippage reserve,
   depth-weighted paired fills, reasons, audit, and compatibility API.
4. **Persistent Paper Alpha — delivered in this branch:** SQLite-funded virtual
   balances, reservations, lifecycle, partial fills, positions, P&L/cost totals,
   restart recovery, exact-once command idempotency, structured persistent audit,
   reconciliation, and a risk-first portfolio UI.
5. **Paper lifecycle hardening — next:** model independent leg latency and stateful
   multi-event partial fills; residual exposure, simulated protective hedge,
   halt/recovery, daily equity snapshots, and migration/backup tooling. Extend the
   canonical engine/service instead of creating a parallel execution architecture.
6. **Long-running validation:** observe public feeds, add deterministic recorded
   fixtures and failure injection, measure beginner comprehension, and validate
   T-Invest sandbox reads with a separate least-privilege setup.
7. **Controlled live admission — future and out of scope:** requires a separate
   explicit acceptance process, authentication/authorization, secret management,
   independent risk review, operational runbooks, and security approval. Nothing
   in this roadmap enables or authorizes live trading.
