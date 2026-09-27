# T-Invest sandbox connection

## Purpose

The platform will first be tried against a dedicated T-Invest API sandbox account.
The sandbox is a separate simulated environment; T-Bank documents it as not changing
real account positions or balances. The sandbox token and sandbox service methods
must be used together. Never configure a production token or production host here.

The connector is wired to local read-only endpoints for sandbox status, accounts,
portfolio, positions, and orders. These endpoints do not submit or cancel orders.
A read-only GET order-price estimate is also available. It queries the sandbox
provider for a one-lot limit-order estimate, but does not place, approve, or reserve
an order. The browser can display this estimate, but it cannot submit or cancel orders.

## Current guardrails

- The HTTP base URL is fixed to `https://sandbox-invest-public-api.tbank.ru`.
- Only an explicit `SandboxService` method allowlist is accepted. Production
  `OrdersService` calls are rejected before a request is sent.
- Account reads and order-price estimates require paper mode, sandbox mode, a configured token, and live
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

The API has no user authentication. Run it only on loopback; never expose it to a
LAN, reverse proxy, or public internet. The routes reject unexpected browser
origins, but Origin checks are not authentication.

## Try the read-only demo locally

Use this integration branch after reviewing its ancestor PRs. From the repository
root, create a local ignored `.env` with only a sandbox token and these safe
settings (never commit the file or share the token):

```dotenv
TRADING_MODE=paper
T_INVEST_SANDBOX=true
LIVE_TRADING_ENABLED=false
T_INVEST_SANDBOX_ORDERS_ENABLED=false
T_INVEST_API_TOKEN=
```

Fill the empty token value only in your private `.env`. Run the backend bound
to `127.0.0.1`, then run the frontend with
`VITE_API_BASE_URL=http://127.0.0.1:8000` as described in README.
Check `/t-invest/sandbox/status` first. In the demo-account panel, load
accounts, select one, open the portfolio, and enter a sandbox instrument UID
(or ticker_classcode) and a positive limit price to view a one-lot estimate.
No order should be sent; the result must be labelled "estimate only" and
must never show risk approval. If no sandbox account is returned, stop and
follow the provider's sandbox-account setup instructions. Do not paste the
token into the browser or logs.

This procedure has not been run with a real sandbox token in CI.
Origin checks do not authenticate local API callers: do not bind to a LAN
interface or expose the port through a proxy.

## Remaining work before sandbox order submission

- Verify the integrated UI and API against a personal sandbox-only account. A
  provider estimate is not a risk decision or evidence of market-data freshness.
- Add a separate review/explicit-confirmation sequence for any future order.
- Persist idempotency keys and sandbox responses in the audit trail; reconcile order state.
- Add local authentication before any mutable sandbox controls are exposed.
- Run mocked tests and CI, then a user-observed integration check using only a
  sandbox token and sandbox account.

References: [GetSandboxOrderPrice](https://developer.tbank.ru/invest/api/sandbox-service-get-sandbox-order-price),
[T-Invest sandbox](https://developer.tbank.ru/invest/intro/developer/sandbox),
[PostSandboxOrder](https://developer.tbank.ru/invest/api/sandbox-service-post-sandbox-order),
[API token types](https://developer.tbank.ru/invest/intro/intro/token).
