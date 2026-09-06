# Explainable paper trading

This is keyless hypothetical simulation over public Binance/Bybit/OKX depth.
There are no real orders, withdrawals or private exchange endpoints. T-Invest
remains separate sandbox/read-only and cannot participate in crypto spreads.

## Local example

Start the backend and frontend using the README commands. Enable the configured
backend client with `VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev` in frontend.
Wait for fresh books; a valid source does not guarantee a profitable spread.

```bash
curl 'http://127.0.0.1:8000/opportunities?explain=true'
curl -X POST http://127.0.0.1:8000/paper/orders/simulate \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTC/USDT","buy_venue":"binance","sell_venue":"okx","notional_usd":"10"}'
curl http://127.0.0.1:8000/paper/orders
curl http://127.0.0.1:8000/paper/fills
curl http://127.0.0.1:8000/reconciliation
curl http://127.0.0.1:8000/audit
```

The explicit request identifies venues, symbol and quote notional only. A rejected
simulation is normal: do not disable freshness or reduce safety thresholds to
force a fill. The old `/opportunities` contract is preserved; `?explain=true`
adds ID, summary, reason code/text, risk-score gate label and a proposed notional
up to $10, available top liquidity and configured trade cap. POST independently
resolves the newest server books and reevaluates the explicit requested size.

## Execution and reconciliation

`PaperExecutionEngine` produces frozen `PaperOrder`, `PaperFill`, `PaperPosition`
and `PaperExecutionReport` records. No connector is an engine dependency. It
consumes asks for the requested buy quote budget and bids for exactly the resulting
base quantity. Both legs must fully fill with sufficient net edge or neither
creates any orders/fills. This is an intentional all-or-reject model, **not** an
assertion that separate exchanges execute atomically.

Financial arithmetic uses Decimal; API financial amounts are decimal strings.

- Expected net % = top-of-book gross % − 0.20% fee estimate − 0.05% reserve.
- Actual estimated fees = 0.10% × buy notional + 0.10% × sell proceeds.
- Depth execution uses volume-weighted average prices. Depth deterioration is
  already reflected in fill notionals and is reported as `depth_slippage_pct`.
- Reserve cost = buy notional × 0.05%; `slippage_cost_usd` exposes this separately.
- Simulated P&L = sell proceeds − buy notional − both fees − reserve cost.
- Simulated net % = P&L / buy notional × 100.
- Reported `slippage_pct` = depth deterioration % + configured reserve %.

Example: buy $100 at 100 and sell the same base at 102. Expected net is 1.75%.
Fees are $0.202, reserve is $0.05, simulated P&L is $1.748 and net is 1.748%.
Fill cash flows reconcile to the hypothetical position P&L after subtracting the
explicit reserve. Fee estimates are not actual exchange/account fee schedules.
USD/USDT/USDC quote amounts are treated as USD equivalents; depeg risk is unmodelled.

Reconciliation includes expected/simulated net, fees, total/depth slippage,
reserve cost, data age, decision reason and execution status. Unavailable metrics
on early rejections use null (simulated result/age), never fabricated fills.
Every fill carries an explanation, identifiers and timestamp.

## Audit and limitations

Market-data producers record `opportunity_detected` and `risk_approved`/`risk_rejected`
for changed snapshots. POST records intent, final gates, `paper_order_created` or
`paper_order_rejected`, one `paper_fill_simulated` event per fill and
`reconciliation_completed`. Events correlate opportunity/execution IDs and include
who, time, reason and decision. `local_paper_user` identifies a local API request,
not an authenticated identity. GET endpoints never write audit events.

This stage has no funded balances, cash reservations, settlement, partial-fill
lifecycle, protective hedging, network-latency execution model or persistence.
Positions describe flat hypothetical paired trades, not an exchange account.
The same snapshot may be reused by independent simulations; depth is not globally
reserved. Duplicate POST requests are separate simulations, so an unknown result
must be checked in the ledger before retrying. History is bounded (1,000 reports
and positions; 10,000 audit events), volatile and single-process. Run one worker.
Binance REST depth lacks an exchange timestamp; its conservative request-start
age still cannot prove upstream freshness. See [source provenance](READ_ONLY_MARKET_DATA.md).

Next: durable event journal and idempotent funded paper accounting with explicit
balance/P&L gates, then partial-fill lifecycle and replay. Live remains locked.

## Verification for this milestone

Baseline after PR #2: 13 Python test files, 152 passed. This milestone adds three
test files without changing any existing test: 16 files, 221 collected/passed,
zero skipped/failed. Checks include full-depth success/rejection, fees and reserve
accounting, stale/future sources, unsupported markets, forged risk/price inputs,
read-only GET contracts, source-error invalidation, no connector order calls,
audit correlation and legacy API compatibility. Ruff, strict mypy and compileall
pass; the frontend passes clean `npm ci` and production build.

A local browser smoke against public data showed all three crypto sources and
safe stale windows with simulation buttons disabled. A real-data paper POST was
rejected with `stale_market_data` (age 1,473 ms against the 1,000 ms limit) and
created zero fills. Successful fill arithmetic is validated on deterministic
normalized-book fixtures; no claim of profitable real-time execution is made.
