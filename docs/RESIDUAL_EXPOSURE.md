## Next paper safety milestone: independent leg outcomes

The current persistent spread simulator deliberately fills both legs by the same
quantity. This is conservative for the alpha, but it is not a model of real
venue atomicity. The next state-machine milestone starts with
`residual_exposure.assess_residual_exposure`.

It accepts independently reported *paper* buy/sell quantities and returns one
of three explicit, non-executing decisions:

| State | Meaning | System action |
|---|---|---|
| `flat` | Both legs have equal quantity. | No hedge. |
| `hedge_required` | Difference is priced and within the configured cap. | Create an explainable **paper hedge proposal** only. |
| `halted` | Mark is absent or exposure exceeds cap. | Block any automatic paper hedge and require review. |

This module cannot call connectors and does not create, cancel, or modify orders.
Before a later state-machine can simulate a hedge it must persist the source
fills, fresh market-data evidence, the residual decision and its audit event in
one transaction. Live trading remains outside this architecture.
