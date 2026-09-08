# Competitor Lessons: Cryptohopper

## Product Principle

We are not copying Cryptohopper. We are building a clearer, safer, and more transparent trading platform.

The platform must be understandable for beginners, useful for advanced users, and strict about risk visibility before any trade execution.

## Mandatory Requirements

1. Every signal must be explainable in plain language.
2. Every opportunity must show net edge after fees and slippage.
3. The user must see why the risk engine rejected a trade.
4. Default mode must be paper/sandbox/read-only.
5. Live trading is allowed only after a separate acceptance procedure.
6. No “magic AI signals” without logic disclosure and risk score.
7. The interface must be understandable to a beginner within 5 minutes.
8. Advanced users must have access to deep metrics.
9. Audit log is mandatory: who, when, and why made or rejected a decision.
10. Marketplace/copy trading, if added later, must have independently verified performance statistics.

## UI Implications

The main dashboard must prioritize:

- live lock status;
- trading mode;
- venue status;
- risk state;
- opportunities;
- net edge;
- rejection reasons;
- audit log;
- paper/sandbox status.

The user should never wonder:

- why the bot acted;
- why the bot did not act;
- whether real money is at risk;
- whether the numbers include fees and slippage.

## Current implementation evidence

The explainable opportunity contract names the source venues, gross edge,
estimated fees/slippage, net edge and the exact risk rejection. An advanced panel
exposes quotes, depth, timestamps and correlated audit events. The only action is
paper simulation, disabled on unusable data. `risk_score` is deliberately a
deterministic gate label (`passed`/`blocked`), not a fabricated AI confidence score.
Reconciliation discloses the difference between a top-of-book estimate and
depth-weighted simulated execution; the slippage reserve is a separate cost.

The ten principles above remain requirements. Beginner usability still needs
actual user testing; a five-minute comprehension claim is a design goal, not a
measured result. No marketplace or copy-trading performance is advertised.
