# ChatGPT authenticated cloud bridge

The Sites Worker accepts only platform-authenticated MCP requests and forwards
them to the fixed Python Native MCP endpoint on Railway. Sites owns OAuth and
the private audience policy. The Worker never calculates financial values or
collects exchange data. Python Decimal strings, evidence quality and execution
locks pass through unchanged.

Configure `MCP_ACCESS_TOKEN` as a Sites secret matching the Railway hosting
token. This is a hosting credential, not an exchange API key. Never put it in
source, plugin manifests or user-visible URLs. Caller credentials are not
forwarded. Source errors, invalid requests and missing configuration fail closed.

Run `npm test` and `npm run build`. Deploy the Worker as a private Sites MCP
capability, then use the canonical plugin returned by Sites. Account installation
and a successful ChatGPT tool call are separate from deployment verification.

Railway currently uses trial credits; availability after credits expire is not
guaranteed. Only BTC/USDT, ETH/USDT and SOL/USDT evidence is supported. Requests
are bounded to 16 KiB and responses to 256 KiB, with a six-second upstream timeout.
