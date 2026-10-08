# Signal readiness audit — 2026-10-08

Base commit: `8fcf0b44340f7ca948d1f610eaab36dec8c2853b`.
Scope: source collection, research worker lifecycle, cached evidence readiness,
existing paper accounting/risk/restart tests, hosted read-only boundary, frontend build.
This is a verified repair batch, not a claim that every project defect is eliminated.

## Reproduced and fixed

- A failing market observer terminated its polling task. Keep the source worker alive,
  expose sanitized `observer_error`, and clear it after a successful observer update.
- A stale refresh returned before notifying the observer that the cached quote was removed.
- Fresh-to-stale reads returned `error=null`; derived stale/future reasons now match status.
- Duplicate venue sources could race on the same state/cache entry; reject them at startup.
- Injected normalized books were trusted beyond the top level; revalidate all depth and time.
- Unexpected candle fetch failures killed the daily worker. Drain the batch, invalidate
  all research inputs and retry without exposing upstream exceptions.
- Repeated daily worker start created an untracked duplicate task; make start idempotent.
- `/health` proved liveness only, while documentation called it readiness. Add cached,
  read-only `/readiness` and allow `/api/readiness` through the hosted GET allowlist.

Regression checks reproduced failures before repair. No risk/freshness threshold was
relaxed, financial accounting was not replaced, and live execution remains unimplemented.

## Verification

| Check | Result |
|---|---|
| Original pytest suite | 463 passed |
| Repaired complete pytest suite | 475 passed |
| `ruff check .` | passed |
| `mypy src` | passed, 48 source files |
| Secret hygiene | passed |
| Python compileall | passed |
| Cloud gateway Node tests | 5 passed |
| `npm ci`, `npm run build` | passed |
| Local Docker smoke | not run: Docker unavailable in the workspace |

Local Python was 3.12 with freshly resolved dependencies; CI remains Python 3.11
with the repository constraints. One upstream Starlette TestClient deprecation
warning remains; changing to a different HTTP client is not part of this repair.

The existing paper tests cover actual SQLite orders/fills, next-day exits, fees,
idempotency after restart, concurrent workers, accounting reconciliation, rollback
on audit failure, loss limits and stale/live-gate rejection. These are controlled
fixtures, not actual OKX demo-account executions or a long-duration profitability test.

## Production and external boundaries

- Railway reported all three services online before this repair. That is liveness,
  not full market readiness; this branch is not yet deployed.
- Production evidence returned fresh OKX observations but missing Binance/Bybit
  classes, with intermittent stale OKX spot. Aggregate quality was insufficient.
  Do not invent cross-venue confirmation or route around access restrictions.
- Direct public-source probes from this workspace all returned sanitized unavailable
  states. That does not prove a production outage or identify an exchange-side cause.
- The user confirmed receipt of the authorized personal Telegram test through
  Composio. This proves that delivery route, not background strategy execution.
- The asynchronous Watch test was requested. Task metadata contains a later run
  timestamp, but this audit cannot retrieve its result/message ID; full background
  end-to-end success remains unverified.
- Watch scheduling is hourly, not continuous intraday monitoring. Reviewing missed
  candles retrospectively cannot deliver an entry that already expired.
- The daily OKX paper robot covers BTC/ETH/LTC and uses completed daily candles.
  The separate Watch prioritizes BTC/ETH/SOL and manual demo signals. They are not
  the same strategy or the same account. Paper fills do not change an OKX demo balance.
- Telegram transport configuration lives outside the repository. No bot token,
  private destination ID or account credential is stored in this audit.
- No merge to main, Railway redeploy, paid subscription or live order was performed.

## Handoff

After review and explicit merge authorization, rerun constrained CI including Docker,
deploy the approved commit, inspect readiness/evidence over repeated refreshes, and
verify a unique background test message ID in the private delivery ledger. Restore
missing sources only after diagnosing availability and permissions; keep trade gates.
Continuous 5/15-minute monitoring requires an explicitly deployed persistent worker,
not simply a shorter unsupported ChatGPT task schedule.
