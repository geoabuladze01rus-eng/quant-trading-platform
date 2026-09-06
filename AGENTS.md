# Engineering and product rules

The canonical Python package is `src/quant_trading_platform/`; do not create
`src/quant_platform/` or rename the package. Preserve existing documents and
avoid unnecessary architecture changes. Work on the requested branch; do not
merge into `main` without explicit authorization.

## Safety

- Keep live trading disabled and default to paper/sandbox/read-only.
- Do not implement real `place_order` or other live execution endpoints.
- Do not add real API keys, log secrets, or commit `.env` files.
- Never request withdrawal permissions or implement withdrawal behavior.
- Keep T-Invest isolated in sandbox/read-only mode.
- Enforce risk gates before any simulated execution; safety changes require
  regression tests. Never remove or weaken existing tests to make checks pass.
- Keep GET endpoints read-only and the dashboard risk-first. Do not add real
  buy/sell buttons or hide rejection reasons behind profit-focused messaging.

## Product principles

Follow [Competitor Lessons: Cryptohopper](docs/COMPETITOR_LESSONS.md).
We are not copying Cryptohopper. We are building a clearer, safer, and more
transparent trading platform.

1. Explain every signal in plain language.
2. Show net edge after fees and slippage for every opportunity.
3. Show why the risk engine rejected a trade.
4. Default to paper/sandbox/read-only.
5. Require a separate acceptance procedure for any future live trading;
   this MVP must keep real execution unimplemented and blocked.
6. Do not present magic AI signals without disclosed logic and a risk score.
7. Make the interface understandable to a beginner within five minutes.
8. Provide deep metrics for advanced users.
9. Maintain an audit log of who, when, and why made or rejected a decision.
10. Require independent verification of performance statistics for any future
    marketplace or copy-trading feature.

## Verification

Run `ruff check .`, `mypy src`, and the complete `pytest` suite from the repository
root. Run `npm ci` and `npm run build` from `frontend/`. Keep CI aligned with
these commands. Report failures and any checks that could not be executed.
