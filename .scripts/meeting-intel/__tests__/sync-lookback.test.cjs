'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { deriveLookbackDays, mapWithConcurrency } = require('../sync-from-granola.cjs');

const now = new Date('2026-07-13T12:00:00.000Z');

test('sync lookback defaults to the recommended fourteen days without a valid lastSync', () => {
  assert.equal(deriveLookbackDays({}, now), 14);
  assert.equal(deriveLookbackDays({ lastSync: null }, now), 14);
  assert.equal(deriveLookbackDays({ lastSync: 'not-a-date' }, now), 14);
  assert.equal(deriveLookbackDays({}, now, 7), 7);
  assert.equal(deriveLookbackDays({}, now, 30), 30);
});

test('sync lookback fetches only the recent delta after the first run', () => {
  assert.equal(deriveLookbackDays({ lastSync: '2026-07-12T12:00:00.000Z' }, now), 2);
  assert.equal(deriveLookbackDays({ lastSync: '2026-07-05T12:00:00.000Z' }, now), 9);
  assert.equal(deriveLookbackDays({ lastSync: '2026-07-12T12:00:00.000Z' }, now, 30), 2);
});

test('sync lookback caps delayed recovery at thirty days', () => {
  assert.equal(deriveLookbackDays({ lastSync: '2026-05-01T12:00:00.000Z' }, now), 30);
});

test('bounded concurrent reads preserve input order and cap active work', async () => {
  let active = 0;
  let peak = 0;
  const results = await mapWithConcurrency([1, 2, 3, 4, 5], 2, async value => {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise(resolve => setTimeout(resolve, value === 1 ? 15 : 1));
    active -= 1;
    return value * 10;
  });

  assert.equal(peak, 2);
  assert.deepEqual(results, [10, 20, 30, 40, 50]);
});
