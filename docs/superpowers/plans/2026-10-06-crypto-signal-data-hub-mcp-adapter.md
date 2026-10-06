# Crypto Signal Data Hub MCP adapter

Branch: feat/crypto-signal-data-hub-mcp. Authority: approved Data Hub design sections
12 and 16. Strict TDD; do not merge main; public read-only evidence only.

## Task 1: Restricted evidence HTTP client

- Write failing tests for allowlisted symbols, exact GET path, no inherited credentials,
  five-second timeout, no redirects, sanitized HTTP/malformed failures, returned identity,
  generated-time freshness and locked execution contract.
- Implement an async client to the existing backend. Only configured loopback HTTP or
  explicit HTTPS origins; no credentials/path/query/fragment in the origin. The caller
  never supplies a URL. No private exchange API keys.
- Fail closed on invalid or unavailable evidence. Monetary floats reject. Strip unknown
  response fields rather than echo raw payloads. Max response 256 KiB, generated age 5s.
- Focused tests then commit.

## Task 2: MCP server and portable private package

- Write failing discovery/call tests. Implement official MCP Python SDK 1.30.0 optional
  dependency, one tool get_signal_evidence(symbol enum BTC/USDT, ETH/USDT, SOL/USDT).
- Tool annotations readOnlyHint=true, destructiveHint=false, idempotentHint=true.
- Use stdio transport for the existing Native Python runtime. Do not mount the trading
  API or create a public HTTP listener. No resources/prompts/write tools.
- Integration test the actual SDK stdio handshake, discovery and safe failing call.
- Portable package uses installed Python entrypoint; preserves existing repo/runtime.
- Focused tests then commit.

## Task 3: Documentation, verification and publication

- Explain how to run alongside the backend and the distinction between an installed
  local process and a cloud connection. Do not claim mobile/automation connectivity
  without a deployed read-only endpoint.
- Full Ruff, mypy, pytest, compileall, secret hygiene; frontend npm ci/build.
- Final independent review and TDD fixes; publish into existing PR #24. Do not merge.
- Hosting: existing source/runtime must be preserved. Sites requires Workers output and
  cannot host this existing Python backend as-is. Without a verified hosted backend,
  stop at a concrete reviewable local adapter and identify the required hosting input.
- Watcher integration remains gated on verified live endpoint/tool availability; no
  scheduled task may count unavailable evidence as confirmation.
