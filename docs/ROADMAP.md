# Roadmap

## A. In main

MVP safety gates, keyless Binance/Bybit/OKX read-only books, source freshness,
explainable paired depth simulation, persistent SQLite virtual account, exact-once
paper command idempotency, audit, accounting reconciliation and halted recovery.
T-Invest remains a sandbox/read-only boundary. No live order implementation exists.

## B. Open Paper Alpha operations PRs

PR #9 adds verified, non-overwriting SQLite snapshots. Its stacked migration/restore
PR adds ordered transactional migrations, strict schema admission, explicit offline
restore, and preservation of a verified pre-restore recovery snapshot. Neither PR
changes paper execution or enables an automatic hedge. The present durable command
still pairs buy/sell fills equally; it does not create independent execution groups.

## C. Before long-running paper testing

Require green GitHub CI including a real Docker smoke run, deterministic recorded
market fixtures, feed soak metrics, an operator restore drill in the actual local
environment, automated daily snapshots with monitored retention, daily equity
snapshots, and a validated least-privilege T-Invest sandbox transport. Independent
leg events must enter the existing SQLite accounting/audit/idempotency transaction
before they can represent real paper exposure. Do not deploy the unauthenticated
mutable paper API publicly.

## D. Separate future live admission

Out of scope and requires a separate threat model and security review,
authentication/authorization, secrets outside the repository, independent risk
review, operational runbooks, a kill switch, gradual rollout, and separate written
owner authorization. No environment-variable-only unlock is acceptable.
