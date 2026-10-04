# OKX spot research signals

The application polls public, keyless OKX `1Dutc` candles for BTC/USDT, ETH/USDT
and LTC/USDT once an hour. `GET /strategies/spot-signals` returns the most recent
cached candidate signals. This endpoint cannot place paper or live orders. There
is no scheduler that turns signals into orders. T-Invest is a separate sandbox.

The last candle must be completed and from yesterday UTC. All 200 daily bars must
be continuous, positive and valid. Any source failure removes all three series;
relative-strength comparison requires matching dates. An old snapshot expires
without falling back to its last good entry. The API labels every result
`paper_only: true` and `live_execution: false`.

The two research rules are deliberately simple and **not optimized**:

- `daily_trend`: review entry if yesterday's close is above its SMA100 and the
  prior 20-day high. Review exit if the close falls below SMA100 or the prior
  10-day low. Otherwise do not create a new entry. It does not know whether the
  account currently holds the coin.
- `relative_strength`: compare average 30/60-day returns among the three coins,
  and select at most one with both positive returns and price above SMA100. If
  none qualify, do not create a new entry. This is a rank, not a portfolio order.

Use `replay_trend` in `backtesting/spot.py` with a **continuous historical daily
candle series** for an exploratory next-open replay. It uses `Decimal`, buys at
the following day's open with explicit slippage and fees, caps each entry at
50% of marked portfolio value, records each simulated trade and models no
intraday stops. Defaults of 0.35% fees, 0.10% spread and 0.10% slippage per side
are deliberately conservative examples,
not the user's account-specific OKX tariff. The latest public candle endpoint
only provides a short sample and is insufficient to establish a durable edge.
For any performance claim, use several years of historical bars, test unseen
periods, compare to buy-and-hold and cash, and then run a forward paper test.
Verify actual fee tier and conversion costs separately.

`OKXHistorySource` pages through the public history endpoint with a bounded
number of requests, and rejects gaps or unconfirmed candles. From an environment
that can reach OKX, run `python scripts/run_spot_research.py --days 1460`. The
report compares long-only weekly 30/60-day rotation, individual trend replays,
a 50%-allocated BTC hold and cash. It reports a holdout period beginning at 70%
of the sample using historical warmup and reset capital. Parameters are fixed,
not selected on the test period. The output contains a simulated trade journal.
No real performance result is included in the source tree; CI tests synthetic
fixtures, not a live venue's historical fills.

The existing persistent paper executor is **paired-spread-only**: its accounting
requires matching buy and sell legs and its realized-PnL rule equates PnL to
USDT cash flow. A directional spot buy does not satisfy either invariant. Do not
route these spot signals through `/paper/orders`; this requires a separately
reviewed directional ledger and reconciliation before automatic paper execution.

These candidates do not authorize an order. In particular, this research module
does not bypass the existing paper risk gates or the permanent live-execution
lock. Public research on momentum motivates the hypotheses; it does not validate
their parameters or expected return on this account.
