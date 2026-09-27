# T-Invest sandbox connection

## Purpose

The platform will first be tried against a dedicated T-Invest API sandbox account.
The sandbox is a separate simulated environment; T-Bank documents it as not changing
real account positions or balances. The sandbox token and sandbox service methods
must be used together. Never configure a production token or production host here.

The connector in this change is a backend foundation. It is not yet wired into a
browser endpoint or a user-facing order form. That integration must be reviewed and
tested separately before the user can operate it from the interface.

## Current guardrails

- The HTTP base URL is fixed to `https://sandbox-invest-public-api.tbank.ru`.
- Only an explicit `SandboxService` method allowlist is accepted. Production
  `OrdersService` calls are rejected before a request is sent.
- Account reads require paper mode, sandbox mode, a configured token, and live
  trading disabled.
- Sandbox limit orders require the separate
  `T_INVEST_SANDBOX_ORDERS_ENABLED=true` setting. It defaults to false.
- Test orders are limit-only and limited to `T_INVEST_SANDBOX_MAX_LOTS`, which
  defaults to one lot and is capped at ten.
- Network errors are sanitized so response text and authorization headers are not
  exposed.
- No real withdrawals, production orders, or real-money API calls are implemented.

## Local configuration

1. In your T-Invest/T-Bank developer settings, create a **sandbox token**, not a
   production or transfer token. The bank displays the token only once. Do not send
   it in chat, paste it into a commit, or add it to `.env.example`.
2. Put it only in your local, ignored `.env` file as `T_INVEST_API_TOKEN=...`.
3. Keep `T_INVEST_SANDBOX=true` and `LIVE_TRADING_ENABLED=false`.
4. Leave sandbox orders disabled until the separate UI/API review and local safety
   checks are complete.
5. Configure the sandbox account ID only after retrieving it through the sandbox
   account list.

The application should be exposed only on loopback while its mutable local paper
and sandbox controls remain unauthenticated.

## Remaining work before personal demo-account testing

- Wire the backend client into an authenticated local-only API.
- Add a clear UI label for the selected sandbox account and virtual balances.
- Add a review/preview/explicit-confirmation sequence for a one-lot limit order.
- Persist request IDs and sandbox responses in the audit trail; reconcile order state.
- Run mocked tests and CI, then a user-observed integration check using only a
  sandbox token and sandbox account.

References: [T-Invest sandbox](https://developer.tbank.ru/invest/intro/developer/sandbox),
[PostSandboxOrder](https://developer.tbank.ru/invest/api/sandbox-service-post-sandbox-order),
[API token types](https://developer.tbank.ru/invest/intro/intro/token).
