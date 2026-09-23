# Residual exposure: review-only model

The current persistent spread simulator deliberately fills both legs by the same
quantity. This is conservative for the alpha, but it is not a model of real
venue atomicity. The next state-machine milestone starts with
`residual_exposure.assess_residual_exposure` and immutable `PaperLegEvent` DTOs.

It accepts independently reported *paper* buy/sell quantities and returns one
of three explicit, non-executing decisions:

| State | Meaning | System action |
|---|---|---|
| `flat` | Both legs have equal quantity. | No hedge. |
| `hedge_required` | Difference has a fresh sourced mark, cap is respected, and accounting is clean. | Show a **non-confirmable** paper hedge proposal for manual review only. |
| `halted` | Mark/source/time is absent, stale/future; cap is exceeded; or reconciliation fails. | No proposal; pause and investigate. |

Each decision discloses the more-filled leg, residual quantity/notional, mark
source/age, configured cap, simulated hedge side, reason code, Russian reason and
the explicit reason no hedge was executed. Equal event IDs replay once;
conflicting duplicates reject. `audit_payload` is an immutable template, not an
automatically persisted audit event.

GET `/paper/residual-exposure` observes the existing saved paired fill journal
without writing. Current canonical paper commands normally show `flat`. A corrupt
or unmatched journal produces `halted` through accounting reconciliation; the
view never repairs balances or creates a hedge. Independent durable leg accounting
and atomic source-event/decision/audit persistence are a later milestone. Live
trading remains outside this architecture.
