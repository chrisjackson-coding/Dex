'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { getMeetingProcessingMode, getMeetingBackfillDays } = require('../lib/config.cjs');
const { getGranolaApiKey } = require('../lib/granola-api-key.cjs');

test('meeting processing mode accepts the canonical object shape', () => {
  assert.equal(getMeetingProcessingMode({ mode: 'manual' }), 'manual');
  assert.equal(getMeetingProcessingMode({ mode: 'automatic' }), 'automatic');
});

test('meeting processing mode accepts the legacy string shape', () => {
  assert.equal(getMeetingProcessingMode('manual'), 'manual');
  assert.equal(getMeetingProcessingMode('automatic'), 'automatic');
});

test('meeting processing mode defaults malformed or missing values to manual', () => {
  assert.equal(getMeetingProcessingMode(), 'manual');
  assert.equal(getMeetingProcessingMode({}), 'manual');
  assert.equal(getMeetingProcessingMode(42), 'manual');
});

test('meeting backfill accepts only the three explicit onboarding choices', () => {
  assert.equal(getMeetingBackfillDays({ backfill_days: 7 }), 7);
  assert.equal(getMeetingBackfillDays({ backfill_days: 14 }), 14);
  assert.equal(getMeetingBackfillDays({ backfill_days: 30 }), 30);
  assert.equal(getMeetingBackfillDays({ backfill_days: 21 }), 14);
  assert.equal(getMeetingBackfillDays(), 14);
});

test('Granola API key uses environment before the vault .env file', t => {
  const vaultRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'dex-granola-key-'));
  t.after(() => fs.rmSync(vaultRoot, { recursive: true, force: true }));
  fs.writeFileSync(path.join(vaultRoot, '.env'), 'GRANOLA_API_KEY="grn_file"\n');

  assert.equal(
    getGranolaApiKey({ env: { GRANOLA_API_KEY: ' grn_environment ' }, vaultRoot }),
    'grn_environment',
  );
  assert.equal(getGranolaApiKey({ env: {}, vaultRoot }), 'grn_file');
});
