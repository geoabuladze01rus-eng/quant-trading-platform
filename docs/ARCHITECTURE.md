# Architecture

The platform has two isolated market contours: crypto (Binance, Bybit, OKX) and Russian instruments through T-Invest. `MARKET_SCOPE=mixed` permits one dashboard, but it does not combine credentials, balances, or order routing.

`connectors` provides read interfaces and deliberately guarded `place_order` methods. `market_data` normalizes feeds, `strategies` produces opportunities, and `risk` is the approval boundary. `paper_trading` implements hypothetical two-leg depth simulation. `audit_log` records signals and decisions. FastAPI exposes read-only dashboard data and one paper-only simulation POST.

There is no live execution implementation in this repository.

## Paper path

Public REST adapters → validated `NormalizedOrderBook` snapshots → spread detector
→ `RiskDecision` → explicit paper POST → `PaperExecutionEngine` → paired simulated
fills → reconciliation → in-memory records and audit.

There is one canonical Python package and one paper execution engine. The engine
does not depend on connectors. It revalidates snapshots, identities, scope, timestamps,
requested size and costs on every call. All-or-reject depth evaluation happens
before any fill is recorded. Normalized books are immutable snapshots; HTTP workers
fetch them off the event loop and publish them on the loop. The async POST does not
yield while calculating or appending its report and audit events.

Producer-side callbacks record newly observed signals and their risk decisions;
unchanged quote pairs are deduplicated. All GET endpoints remain side-effect free.
`explainability` converts deterministic decisions into human-readable reasons, not
an AI forecast or numeric probability. `/opportunities?explain=true` is an opt-in
contract extension so legacy clients and canonical contract tests remain valid.

The service is a single-process local-development prototype. Paper history is
bounded to 1,000 reports/positions; audit keeps the latest 10,000 events. Neither
has persistence, cross-process consistency nor transactional crash recovery.
See [paper trading](PAPER_TRADING.md) for accounting assumptions and next steps.
