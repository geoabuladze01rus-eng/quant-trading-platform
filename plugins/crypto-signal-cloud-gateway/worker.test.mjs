import assert from 'node:assert/strict';
import test from 'node:test';
import worker from './worker.mjs';

const env = { MCP_ACCESS_TOKEN: 'test-only-token-at-least-32-characters' };
const call = { jsonrpc: '2.0', id: 1, method: 'tools/call', params: {
  name: 'get_signal_evidence', arguments: { symbol: 'BTC/USDT' },
} };
function request(body = call, identity = true) {
  return new Request('https://gateway.example/mcp', {
    method: 'POST', headers: {
      'Content-Type': 'application/json', 'Authorization': 'Bearer caller-secret',
      ...(identity ? { 'oai-authenticated-user-id': 'site-scoped-test-user' } : {}),
    }, body: JSON.stringify(body),
  });
}

test('missing trusted identity or hosted token prevents backend access', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('Unauthorized backend access'); };
  try {
    assert.equal((await worker.fetch(request(call, false), env)).status, 401);
    assert.equal((await worker.fetch(request(), {})).status, 503);
  } finally { globalThis.fetch = previous; }
});

test('tool call forwards to the fixed Native MCP endpoint without caller credentials', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, 'https://crypto-signal-data-hub-production.up.railway.app/mcp/');
    assert.equal(options.headers.Authorization, `Bearer ${env.MCP_ACCESS_TOKEN}`);
    assert.equal(options.headers['oai-authenticated-user-id'], undefined);
    assert.equal(options.redirect, 'error');
    assert.deepEqual(JSON.parse(options.body), call);
    return Response.json({ jsonrpc: '2.0', id: 1, result: {
      structuredContent: { quality: { status: 'insufficient' },
        execution: { paper_only: true, live_execution: false } },
    } });
  };
  try {
    const response = await worker.fetch(request(), env);
    assert.equal(response.status, 200);
    const result = (await response.json()).result.structuredContent;
    assert.equal(result.quality.status, 'insufficient');
    assert.equal(result.execution.live_execution, false);
  } finally { globalThis.fetch = previous; }
});

test('unsupported tools, symbols and methods never reach Native server', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('Invalid request escaped gateway'); };
  try {
    for (const body of [
      { ...call, method: 'place_order' },
      { ...call, params: { name: 'place_order', arguments: {} } },
      { ...call, params: { name: 'get_signal_evidence', arguments: { symbol: 'LTC/USDT' } } },
    ]) assert.equal((await worker.fetch(request(body), env)).status, 400);
  } finally { globalThis.fetch = previous; }
});

test('network failures and upstream bodies become sanitized closed failures', async () => {
  const previous = globalThis.fetch;
  try {
    for (const failure of [
      async () => { throw new Error('private-upstream-trace'); },
      async () => new Response('private-upstream-trace', { status: 500 }),
    ]) {
      globalThis.fetch = failure;
      const response = await worker.fetch(request(), env);
      assert.equal(response.status, 502);
      assert.ok(!(await response.text()).includes('private-upstream-trace'));
    }
  } finally { globalThis.fetch = previous; }
});

test('discovery contains no private data and supports the Sites provisioning probe', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => Response.json({ jsonrpc: '2.0', id: 2, result: { tools: [] } });
  try {
    const response = await worker.fetch(request({ jsonrpc: '2.0', id: 2, method: 'tools/list' }, false), env);
    assert.equal(response.status, 200);
  } finally { globalThis.fetch = previous; }
});
