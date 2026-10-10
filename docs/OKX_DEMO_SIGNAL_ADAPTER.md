# OKX Signal Bot demo adapter — not deployed or connected

This optional perpetual-futures experiment does not replace the spot paper
controller. No import or scheduled worker connects it to the existing engine.
LIVE remains OFF. The default is redacted preview only.

The only destination in code is https://www.okx.com/pap/algo/signal/trigger.
The live webhook is not configurable. Entry is BTC-USDT-SWAP, long only,
market, explicit quote-currency margin <= 10 USDT. Only fresh HIGH/VERY_HIGH
signals with RR >= 2 and bounded volatility, size and risk pass validation.
These are mechanical gates, not evidence of a profitable strategy.

Official schema and field precedence:
https://www.okx.com/help/signal-bot-alert-message-specifications
Bot settings take precedence over signal settings. Therefore the operator must
verify the specific demo bot: BTC perpetual, 1x, fixed 10 USDT entry, no multiple
entry, maximum one position, price-based TP 2% and SL 1%, demo funding 50 USDT.
Code cannot verify these settings or the account mode from a signal token alone.
Do not attach the same signal token to several bots.

Before integration:

1. Add a separate demo sender service from feat/direct-okx-control, not main.
   This module alone is not a runnable service or scheduled strategy.
2. The user enters the rotated token privately into the intended service's
   sealed OKX_DEMO_SIGNAL_TOKEN variable. Never put it in chat, source, examples
   or logs. No environment variable or token has been configured by this change.
3. Add authenticated configuration, exchange-state reconciliation and durable
   ledger on a persistent volume before enabling any worker. Do not use the
   existing spot paper EXECUTE receipt as proof of futures risk acceptance.
4. Keep enabled=False until the exact demo bot and single-token mapping have
   been independently confirmed. No HTTP request is made during tests.
5. The first external test requires a separate, explicit operator action. Check
   the OKX signal log AND position size. A 2xx HTTP response is not a fill.

The ledger reserves a single slot before network I/O. A timeout, crash,
redirect, rejection or acknowledgment keeps the slot locked. There is no
automatic POST retry or automatic slot reset: an ambiguous result could already
have placed an order. Verify exchange state before any further entry. Closure
reconciliation and exit dispatch are intentionally not implemented yet. TP/SL
on the demo bot is not a substitute for that reconciliation.
