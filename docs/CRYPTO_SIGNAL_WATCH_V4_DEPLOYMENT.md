# Crypto Signal Watch v4 — deployed preview acceptance, 2026-10-08

This report supersedes earlier statements that v4 had not been deployed.
Implementation remains on feat/crypto-signal-data-hub-mcp; main is unchanged.

## Completed work

The existing isolated Railway UI Preview service crypto-signal-data-hub was updated
from pinned 702d498 to tested ca5cbe9711eef0fb815da1a189aecf8311d7dde2.
No production-main Data Hub/MCP/gateway service in the separate Data Hub project was changed.
Public dashboard: https://crypto-signal-data-hub-production.up.railway.app
Deployment 183f0396-16fd-463a-a8c5-d7239dd5920f is SUCCESS; actual build and runtime logs
show hosted Docker image built, application startup completed and GET /api/health 200.
An earlier attempted deployment 41fb3251 failed; its returned logs show completed build
but no runtime log. The subsequent successful deployment is the acceptance reference.

Explicit settings: paper, live execution disabled, live acceptance gate disabled,
OKX automatic paper execution disabled, public read-only crypto data and v4 scanner enabled.
No exchange API key was configured or read. Existing MCP access configuration was preserved
without reading its value. No Telegram dispatch or notification switch was performed.

A persistent 500MB journal volume was attached at /app/data. Railway defaulted the disk
to sfo and consequently deployed the service in sfo despite the prior European service
configuration. Reuse of the feature-branch preview avoids the new-service resource limit.
The original working main service and its data remain separate.

## Actual public observations

The local keyless CLI and web lookup could not access the deployed origin from their
network environment. Firecrawl public GET reads with maxAge=0 successfully returned JSON
for /api/health, /api/crypto-signal-watch, /api/venues and evidence for BTC/ETH/SOL.
These are sampled deployment observations, not a completed fifteen-minute readiness run.

- Health: status=ok, trading_mode=paper, live_trading=locked.
- Watch: paper_only=true, live_execution=false; all three asset states ok, twelve candidates
  with explicit rejection reasons, historical journal accumulating (24 rows at first sample).
- No accepted HIGH or VERY HIGH candidate in the observed sample. SOL Liquidity Sweep
  had score 40 and insufficient_independent_evidence; other sampled setups were unconfirmed.
- All six external Native providers have no_data: no permanent executor is bound.
- BTC/ETH/SOL core quality is insufficient. OKX spot/derivatives work; Binance/Bybit
  REST sources are error. One ETH sample also expired its OKX spot deadline correctly.
- Binance WS is error; Bybit WS alternates connected/error; OKX WS receives data.
- Public responses preserve Decimal strings, per-venue identity and original clocks.

No missing source was relabelled healthy, no threshold/freshness requirement was weakened,
and source outages were not turned into bullish or neutral confirmations. These facts
explain why this deployment cannot yet qualify for full signal delivery acceptance.

## Prepared migration — not applied

A new 500MB volume signal-watch-v4-eu-journal, id f1a7dade-8ed4-48f7-b6c9-5679a6febe2e,
was provisioned explicitly in europe-west4. Six staged changes on this preview service
prepare one EU replica, no sleep, detachment of the SFO journal and attachment of EU journal
at /app/data. The SFO volume 07874b69-eb9c-497e-822e-83c5748a1eae remains an archive;
its diagnostic history is not copied to the fresh EU disk. No disk or data was deleted.

Patch 2eb994af-c932-43ab-be15-007ead310b74 in environment
860d60db-5919-4ad3-98f5-d8d3896015fd was NOT committed. Railway accept-deploy returned
“Cancelled — the user did not approve this action. No changes were made.”
The currently running successful deployment remains in SFO. No direct-write bypass
was attempted. Approval of that exact prepared migration is the next deployment step.
Moving to Europe is a diagnostic placement correction, not proof REST will recover.

## Verification and unresolved acceptance

The deployed code ca5cbe9 passed 652 backend tests and 11 frontend tests, Ruff, mypy
(62 source files), compileall, staged secret hygiene, npm ci/build and GitHub CI including
Docker smoke (run 37777893855). Repository coverage 91.58%, signal_watch 93.00%,
acceptance 95.65%, readiness 94.56%; frontend coverage unmeasured.
This continuation changes documentation only; no new implementation coverage claim.

Full completion remains blocked by the cancelled EU migration, failed deployed core
sources and absent permanent authorized Native executor/vendor producers.
CryptoAudit/TradingCursor samples lack upstream clocks; Exa/Blockscout producer mappings
remain unverified. The existing notification workflow remains unchanged as required.
Continuous operation, real exact-horizon outcome acceptance and Telegram delivery are
not claimed. PR #27 stays draft and unmerged.

After preparing this report, all required local commands were executed again: Ruff,
mypy 62 files, pytest 652 passed, compileall, staged secret hygiene, npm ci, npm test
11 passed and npm run build all exited 0. No additional coverage run was required
because source/test code was unchanged; percentages above are the actual ca5cbe9 run.
