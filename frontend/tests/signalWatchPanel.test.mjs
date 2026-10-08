import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { SignalWatchView } from '../src/SignalWatchPanel.tsx';
import { parseSignalWatch } from '../src/signalWatch.ts';
import { fixture, now } from './fixtures.mjs';

test('view shows domain scores and rejected ANTI MISS history without execution controls', () => {
  const raw = fixture();
  raw.journal.rows[0].confidence = 'REJECTED';
  raw.journal.rows[0].rejected_reason = 'insufficient_independent_evidence';
  raw.journal.rows[0].missed_reason = 'near_threshold';
  const html = renderToStaticMarkup(createElement(SignalWatchView, {
    snapshot: parseSignalWatch(raw, now), elapsedMs: 0, error: '',
  }));
  assert.match(html, /Crypto Signal Watch v4/);
  assert.match(html, /VERY HIGH/);
  assert.match(html, /A: 25/);
  assert.match(html, /ANTI MISS/);
  assert.match(html, /near_threshold/);
  assert.match(html, /insufficient_independent_evidence/);
  assert.match(html, /Расчётный markout/);
  assert.match(html, /Наблюдённые результаты/);
  assert.doesNotMatch(html, /<button|<form|BUY|SELL/);
});
test('expired current candidates are hidden while historical research remains explicit', () => {
  const html = renderToStaticMarkup(createElement(SignalWatchView, {
    snapshot: parseSignalWatch(fixture(), now), elapsedMs: 1001, error: '',
  }));
  assert.match(html, /Данные устарели/);
  assert.match(html, /История кандидатов/);
  assert.match(html, /не являются текущими сигналами/);
});
test('outages remove current data and expose an unavailable state', () => {
  const html = renderToStaticMarkup(createElement(SignalWatchView, {
    snapshot: null, elapsedMs: 0, error: 'Данные v4 недоступны',
  }));
  assert.match(html, /role="alert"/);
  assert.match(html, /Данные v4 недоступны/);
  assert.doesNotMatch(html, /VERY HIGH|Momentum/);
});
