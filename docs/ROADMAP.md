# Roadmap

## A. In main

MVP safety gates, keyless Binance/Bybit/OKX read-only books, source freshness,
explainable paired depth simulation, persistent SQLite virtual account, exact-once
paper command idempotency, audit, accounting reconciliation and halted recovery.
T-Invest remains a sandbox/read-only boundary. No live order implementation exists.

## B. After PR #5 and its stacked foundation PR are merged

PR #5 contributes only the pure `flat` / `hedge_required` / `halted` residual model;
it does not execute a hedge. The foundation PR adds checked mark source/freshness,
immutable simulated leg-event DTOs, deterministic event replay, a non-confirmable
paper hedge proposal, a read-only saved-journal view, beginner guidance, loopback
Docker binding, pinned Python CI dependencies, secret hygiene and Docker smoke CI.
The present durable command still pairs buy/sell fills equally; it does not create
independent execution groups or automatic hedges.

## C. Before long-running paper testing

Require green GitHub CI including a real Docker smoke run, deterministic recorded
market fixtures, failure injection, feed soak metrics, operator backup/restore drill,
daily equity snapshots, versioned migrations, and a validated least-privilege
T-Invest sandbox transport. Independent leg events must enter the existing SQLite
accounting/audit/idempotency transaction before they can represent real paper
exposure. Do not deploy the unauthenticated mutable paper API publicly.

## D. Separate future live admission

Out of scope and requires a separate threat model and security review,
authentication/authorization, secrets outside the repository, independent risk
review, operational runbooks, a kill switch, gradual rollout, and separate written
owner authorization. No environment-variable-only unlock is acceptable.
