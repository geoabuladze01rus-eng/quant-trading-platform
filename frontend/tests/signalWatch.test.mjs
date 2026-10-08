import assert from 'node:assert/strict';
import test from 'node:test';
import { readSignalWatch, parseSignalWatch, visibleAssets, visibleProviders } from '../src/signalWatch.ts';

import { fixture, candidate, now } from './fixtures.mjs';

test('financial strings are preserved and current candidates expire in the browser', () => {
  const raw = fixture();
  raw.assets['BTC/USDT'].candidates[0].score = '84.12345678901234567890123456';
  const parsed = parseSignalWatch(raw, now);
  assert.equal(parsed.assets['BTC/USDT'].candidates[0].score, '84.12345678901234567890123456');
  assert.equal(visibleAssets(parsed, 1000)['BTC/USDT'].candidates.length, 1);
  assert.equal(visibleAssets(parsed, 1001)['BTC/USDT'].status, 'stale');
  assert.equal(visibleAssets(parsed, 1001)['BTC/USDT'].candidates.length, 0);
  assert.equal(parsed.journal.rows.length, 1);
});
test('unsafe, malformed and mismatched candidate responses fail closed', () => {
  for (const change of [
    x => x.live_execution = true,
    x => x.paper_only = false,
    x => x.assets['BTC/USDT'].candidates[0].asset = 'ETH/USDT',
    x => x.assets['BTC/USDT'].candidates[0].score = 85,
    x => x.assets['BTC/USDT'].candidates[0].score = 'NaN',
    x => x.assets['BTC/USDT'].candidates[0].live_execution = true,
    x => x.assets['BTC/USDT'].valid_until_ms = now - 1,
    x => x.assets['BTC/USDT'].candidates[0].confidence = 'BUY',
    x => x.assets['BTC/USDT'].candidates[0].confidence = 'REJECTED',
    x => x.journal.rows = Array(101).fill(candidate),
    x => x.outcomes.kind = 'realized_profit',
  ]) {
    const raw = fixture(); change(raw); assert.throws(() => parseSignalWatch(raw, now));
  }
});
test('watch transport uses only credential-free uncached GET and sanitizes errors', async () => {
  let request;
  const result = await readSignalWatch('https://example.test/api', async (url, options) => {
    request = { url, options }; return new Response(JSON.stringify(fixture()));
  }, () => now);
  assert.equal(result.paper_only, true);
  assert.equal(request.url, 'https://example.test/api/crypto-signal-watch');
  assert.equal(request.options.method, 'GET');
  assert.equal(request.options.credentials, 'omit');
  assert.equal(request.options.cache, 'no-store');
  assert.equal(request.options.redirect, 'error');
  await assert.rejects(readSignalWatch('https://example.test/api', async () => {
    throw new Error('private upstream details');
  }), /Данные v4 недоступны/);
});
test('demo and disabled states never invent candidates or healthy providers', async () => {
  const result = await readSignalWatch(undefined, () => assert.fail('demo must not fetch'));
  assert.equal(result.status, 'not_connected');
  assert.deepEqual(result.assets, {});
  assert.deepEqual(parseSignalWatch({ status: 'disabled', assets: {},
    paper_only: true, live_execution: false }).assets, {});
});

test('provider health expires on its original source deadline in the browser', () => {
  const raw = fixture();
  raw.providers[0] = { source: 'Gina', symbol: 'BTC/USDT', status: 'ok',
    timestamp_ms: now - 4000, valid_until_ms: now + 1000 };
  const parsed = parseSignalWatch(raw, now);
  assert.equal(parsed.providers[0].valid_until_ms, now + 1000);
  assert.equal(visibleProviders(parsed, 1000)[0].status, 'ok');
  assert.equal(visibleProviders(parsed, 1001)[0].status, 'stale');
});

test('oversize responses are rejected before displaying otherwise valid candidates', async () => {
  const raw = fixture(); raw.padding = 'x'.repeat(1048576);
  await assert.rejects(readSignalWatch('/api', async () => new Response(JSON.stringify(raw))),
    /Данные v4 недоступны/);
});

test('old and future self-consistent snapshots cannot replay as fresh candidates', () => {
  assert.throws(() => parseSignalWatch(fixture(), now + 3600000));
  assert.throws(() => parseSignalWatch(fixture(), now - 1));
});
test('receipt age consumes the remaining candidate and provider freshness budget', () => {
  const raw = fixture();
  raw.providers[0] = { source: 'Gina', symbol: 'BTC/USDT', status: 'ok',
    timestamp_ms: now - 4000, valid_until_ms: now + 1000 };
  const parsed = parseSignalWatch(raw, now + 900);
  assert.equal(visibleAssets(parsed, 100)['BTC/USDT'].status, 'ok');
  assert.equal(visibleAssets(parsed, 101)['BTC/USDT'].status, 'stale');
  assert.equal(visibleProviders(parsed, 101)[0].status, 'stale');
});
