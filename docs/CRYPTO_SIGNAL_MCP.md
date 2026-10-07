# Crypto Signal Data Hub — private MCP adapter

Hosted follow-up: [Hosted Data Hub](HOSTED_DATA_HUB.md) describes the combined
dashboard/read-only gateway and authenticated HTTP MCP. Native stdio remains the
default. The historical deployment-pending section below is superseded by that
follow-up: Railway now serves the dashboard and `/mcp/`, while ChatGPT account
installation and unattended Watch integration remain unverified.

This adapter exposes exactly one read-only MCP tool: `get_signal_evidence(symbol)`.
Symbols are the enum BTC/USDT, ETH/USDT, SOL/USDT. It returns the existing Data Hub
contract, without opening trades, polling exchanges or importing the trading API.

## Native installation and startup

From the repository, using Python 3.11+:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[mcp]'
python -m quant_trading_platform.mcp.server
```

The last command is a stdio MCP process, not an interactive shell or HTTP server.
A compatible Native MCP host starts it and speaks the protocol over stdin/stdout.
Activate/install the package in the Python environment that the host launches.
The portable private package is `plugins/crypto-signal-data-hub/`; its `mcp.json`
uses `python -m quant_trading_platform.mcp.server`. No absolute machine paths,
exchange keys, cached credentials, tokens or placeholder remote URLs are bundled.
The backend must already be running separately. This module does not start it.

Default backend origin: `http://127.0.0.1:8000`.
To select a verified remote evidence backend, configure `SIGNAL_EVIDENCE_ORIGIN` in
its launching environment. Only loopback HTTP or explicit HTTPS origins are accepted;
URL credentials, paths, queries and fragments are rejected. Do not expose the full
unauthenticated paper command API on a public domain to make this bridge work.

The adapter pins the official MCP Python SDK to 1.30.0. It is optional for ordinary
backend runtime installations and included in development dependencies for tests.
Default stdio startup opens no listener. `MCP_TRANSPORT=streamable-http` enables
standalone HTTP compatibility; that standalone entrypoint provides no hosted
authentication. Do not publish it or `Dockerfile.mcp` directly. Use the protected
gateway in `Dockerfile.hosted` for remote deployment.

## Contract and safety

Discovery advertises readOnlyHint=true, destructiveHint=false, idempotentHint=true.
There are no resources, prompts, order tools or arbitrary URL/path inputs.

Calls issue one credential-free GET to `/signal-evidence/{encoded canonical symbol}`.
The request cannot inherit client authentication, cookies or headers. Redirects are
not followed. Total call time and request timeout are bounded to five seconds; decoded
response content is bounded to 256 KiB.

The response must identify the requested symbol, use a positive generated timestamp
no older than five seconds and not in the future, retain paper/live execution locks,
and contain complete source rows, consistent missing/stale labels, native liquidation
units and deterministic quality. Source freshness is conservatively limited to 1 second
for spot and 6 minutes for derivatives, matching backend defaults; a more permissive
backend configuration does not relax the adapter. Monetary fields
remain exact decimal strings; floating and nonfinite values reject. Unknown response
fields are removed using a positive field allowlist; known source errors remain stable
codes. HTTP errors, malformed/untrusted payloads and unavailable sources produce the
sanitized tool error `signal_evidence_unavailable`, without raw bodies, URLs, credentials
or upstream traces.

A valid `insufficient` response is evidence that data is unavailable, not a signal.
It must never count as an independent confirmation. Degraded evidence requires explicit
caller acceptance of the missing/stale source classes. Data Hub is one evidence source;
other independent trend/technical/news/risk confirmations remain required.

## Connection status and deployment boundary

The local package and stdio server can be used by a Native MCP host. They are not a
mobile/cloud connection and are not installed into the user's account by committing files.
A closed notebook cannot sustain a local backend/MCP process or scheduled watcher.

The repository has no verified deployed read-only Data Hub URL. Sites hosting expects
Workers-compatible output, while this existing backend is a Python runtime with continuous
polling and WebSocket collectors. Keep this codebase and runtime; do not substitute a
second TypeScript market-data stack or a fabricated remote endpoint.

Before cloud/mobile integration: provision a persistent host for the read-only backend
and authenticated remote MCP transport (or a safely hosted gateway), test source
connectivity and caller access, then publish/connect the private plugin. A private MCP
connection must expose only this evidence tool. Hosted authentication is separate from
exchange API keys; no private exchange credentials are required.

Scheduled Crypto Signal Watch integration remains pending that verified connection.
Do not modify a watcher to count a missing/unconnected adapter as neutral evidence.

## Verification

Focused tests cover the actual SDK discovery/call surface and stdio subprocess handshake,
plus the bridge's public request, identity, freshness, locks and bounded failure handling.
Full repository checks remain Ruff, mypy, pytest, compileall, secret hygiene and frontend
npm ci/build. Mocked/empty-state tests do not prove live exchange availability.
