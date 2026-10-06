const upstream = 'https://crypto-signal-data-hub-production.up.railway.app/mcp/';
const methods = new Set(['initialize', 'ping', 'tools/list', 'tools/call',
  'notifications/initialized', 'resources/list', 'prompts/list']);
const symbols = new Set(['BTC/USDT', 'ETH/USDT', 'SOL/USDT']);
const failure = (status, message) => Response.json({ error: message }, { status });

async function boundedBody(body, limit) {
  if (!body) return '';
  const reader = body.getReader();
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); throw new Error('Body limit'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
}

export default {
  async fetch(request, env) {
    if (!request.headers.get('oai-authenticated-user-id')) return failure(401, 'Authentication required');
    if (typeof env.MCP_ACCESS_TOKEN !== 'string' || env.MCP_ACCESS_TOKEN.length < 32)
      return failure(503, 'Gateway unavailable');
    const path = new URL(request.url).pathname;
    if (path === '/' && request.method === 'GET')
      return Response.json({ service: 'Crypto Signal Data Hub', mode: 'read-only', endpoint: '/mcp' });
    if (!['/mcp', '/mcp/'].includes(path) || request.method !== 'POST')
      return failure(405, 'Unsupported request');
    let rpc;
    try {
      rpc = JSON.parse(await boundedBody(request.body, 16384));
      if (!rpc || rpc.jsonrpc !== '2.0' || !methods.has(rpc.method)) throw new Error('Invalid RPC');
      if (rpc.method === 'tools/call' && (rpc.params?.name !== 'get_signal_evidence' ||
          !symbols.has(rpc.params?.arguments?.symbol) ||
          Object.keys(rpc.params.arguments).some(key => key !== 'symbol')))
        throw new Error('Invalid tool');
    } catch { return failure(400, 'Invalid MCP request'); }
    try {
      const response = await fetch(upstream, {
        method: 'POST', redirect: 'error', signal: AbortSignal.timeout(6000),
        headers: { Authorization: `Bearer ${env.MCP_ACCESS_TOKEN}`,
          'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
        body: JSON.stringify(rpc),
      });
      if (response.status === 202 && rpc.id === undefined) return new Response(null, { status: 202 });
      if (response.status !== 200) throw new Error('Upstream unavailable');
      const result = JSON.parse(await boundedBody(response.body, 262144));
      if (result.jsonrpc !== '2.0' || result.id !== rpc.id || (!('result' in result) && !('error' in result)))
        throw new Error('Invalid upstream response');
      return Response.json(result);
    } catch { return failure(502, 'Evidence service unavailable'); }
  },
};
