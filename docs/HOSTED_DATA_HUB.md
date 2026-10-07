# Hosted Data Hub

`Dockerfile.hosted` builds the React dashboard and the existing Python backend in
one container. Start `quant_trading_platform.mcp.hosted:hosted_app` with Uvicorn
`--factory`, port 8000. `/api/health` is the readiness endpoint. The backend's
existing lifespan owns polling and liquidation collectors; no duplicate data stack
or extra collector service is created.

The public dashboard uses `/api`. A positive path allowlist permits only GET reads.
All commands, unlisted paths and backend documentation routes are rejected before
dispatch. The original backend remains available for local Native use. Paper UI
command attempts on the hosted dashboard are rejected; this deployment does not
enable paper automation or live execution. Paper history in this trial container
is ephemeral because no persistent volume is attached.

Streamable HTTP MCP is mounted at `/mcp/`, stateless with JSON responses, and still
advertises only `get_signal_evidence`. Its adapter requests the existing backend
through an in-process ASGI client, preserving Decimal strings and fail-closed
validation. No private network HTTP origin exception is needed.

Set `MCP_ACCESS_TOKEN` to a randomly generated token of at least 32 characters in
the hosting environment. Missing/short configuration returns 503; missing or wrong
Bearer authorization returns 401. Never put this token in the frontend, repository,
logs or PR. This is a hosting access token, not an exchange API key. Railway's
`RAILWAY_PUBLIC_DOMAIN` is added to SDK DNS-rebinding protection; that protection
remains enabled.

Compatible remote MCP clients must send `Authorization: Bearer <token>`. This is
not OAuth: ChatGPT account installation and scheduled Watch integration are not
completed by deploying this container. Do not claim mobile connectivity until
the client supports this authentication and discovery/call is verified.

Trial hosting uses the account's existing credits and stops when they expire or
are exhausted. One replica, 0.5 GB memory cap and 1 CPU cap limit resources but do
not establish a monthly spending budget or guarantee permanent free service.
No subscription or card is required by this deployment.

Verification: gateway tests reject command dispatch, unknown routes and unauthorized
MCP; actual HTTP discovery and the real empty-state evidence backend are exercised.
Health readiness proves startup, not exchange data completeness. Inspect evidence
quality independently before using a signal.
