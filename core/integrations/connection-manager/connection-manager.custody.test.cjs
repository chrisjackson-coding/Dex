'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const TMP_VAULT = fs.mkdtempSync(path.join(os.tmpdir(), 'dex-cm-custody-'));
process.env.DEX_VAULT = TMP_VAULT;
process.env.DEX_CM_NO_KEYCHAIN = '1';

const store = require('./token-store.cjs');
const { cmdStatus } = require('./connect.cjs');

const CREDENTIALS_DIR = path.join(TMP_VAULT, 'System', 'credentials');
const SEALED_MARKER = path.join(CREDENTIALS_DIR, '.dex-cm.sealed');
const KEY_FILE = path.join(CREDENTIALS_DIR, '.dex-cm.key');
const REGISTRY_FILE = path.join(CREDENTIALS_DIR, 'connections.json');
const OAUTH_APPS_FILE = path.join(CREDENTIALS_DIR, 'oauth-apps.json');
const TOKENS_DIR = path.join(CREDENTIALS_DIR, 'tokens');
let sealedHostAfterMigration;

test.after(() => {
  fs.rmSync(TMP_VAULT, { recursive: true, force: true });
});

test('sealed marker without a reachable host fails closed with a typed error', () => {
  store.saveApiKey(
    'sealed-host-required',
    { apiKey: 'FAKE-SEALED-HOST-REQUIRED' },
    { provider: 'sealed-host-required' }
  );
  fs.writeFileSync(
    SEALED_MARKER,
    JSON.stringify({ v: 1, custody: 'sealed-host' }),
    { mode: 0o600 }
  );
  const tokenPath = path.join(TOKENS_DIR, 'sealed-host-required.json');
  const before = fs.readFileSync(tokenPath);

  assert.throws(
    () => store.loadToken('sealed-host-required'),
    (error) => error && error.code === 'DEX_CM_SEALED_HOST_REQUIRED'
  );
  assert.deepEqual(fs.readFileSync(tokenPath), before);
  assert.equal(
    fs.readdirSync(TOKENS_DIR).some((name) => name.includes('.corrupt-')),
    false
  );
});

test('sealed host availability must be synchronously confirmed', () => {
  const legacyTrustMacKey = store.trustMacKey();
  store.setHostDecryptor({
    available: async () => true,
    decrypt: () => JSON.stringify({ apiKey: 'FAKE-ASYNC-HOST' }),
    mac: (payload) => crypto
      .createHmac('sha256', legacyTrustMacKey)
      .update(payload)
      .digest('base64'),
  });

  assert.throws(
    () => store.loadToken('sealed-host-required'),
    (error) => error && error.code === 'DEX_CM_SEALED_HOST_REQUIRED'
  );
});

test('sealed marker with a reachable host decrypts through the host seam', () => {
  const calls = [];
  const legacyTrustMacKey = store.trustMacKey();
  store.setHostDecryptor({
    available: () => true,
    decrypt(connId, envelope) {
      calls.push({ connId, envelope });
      return JSON.stringify({ kind: 'api_key', apiKey: 'FAKE-FROM-SEALED-HOST' });
    },
    mac(payload) {
      return crypto
        .createHmac('sha256', legacyTrustMacKey)
        .update(payload)
        .digest('base64');
    },
  });

  assert.equal(
    store.loadToken('sealed-host-required').apiKey,
    'FAKE-FROM-SEALED-HOST'
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].connId, 'sealed-host-required');
});

test('an unmarked store keeps the existing in-process decrypt path', () => {
  fs.rmSync(SEALED_MARKER);
  store.setHostDecryptor({
    available: () => true,
    decrypt() {
      throw new Error('unmarked stores must not call the sealed host');
    },
  });

  assert.equal(
    store.loadToken('sealed-host-required').apiKey,
    'FAKE-SEALED-HOST-REQUIRED'
  );
});

function createMockHostDecryptor() {
  const masterKey = crypto.randomBytes(32);
  const macKey = Buffer.from(
    crypto.hkdfSync('sha256', masterKey, Buffer.alloc(0), 'dex-cm-trust-mac-v1', 32)
  );
  const calls = { seal: [], decrypt: [], mac: [] };
  return {
    calls,
    available: () => true,
    seal(connId, plaintext, aad) {
      calls.seal.push(connId);
      const iv = crypto.randomBytes(12);
      const cipher = crypto.createCipheriv('aes-256-gcm', masterKey, iv);
      cipher.setAAD(Buffer.from(aad, 'utf8'));
      const data = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
      return {
        v: 1,
        custody: 'sealed-host',
        aad,
        iv: iv.toString('base64'),
        tag: cipher.getAuthTag().toString('base64'),
        data: data.toString('base64'),
      };
    },
    decrypt(connId, envelope) {
      calls.decrypt.push(connId);
      assert.equal(envelope.custody, 'sealed-host');
      const decipher = crypto.createDecipheriv(
        'aes-256-gcm',
        masterKey,
        Buffer.from(envelope.iv, 'base64')
      );
      decipher.setAAD(Buffer.from(envelope.aad, 'utf8'));
      decipher.setAuthTag(Buffer.from(envelope.tag, 'base64'));
      return Buffer.concat([
        decipher.update(Buffer.from(envelope.data, 'base64')),
        decipher.final(),
      ]).toString('utf8');
    },
    mac(payload) {
      calls.mac.push(payload);
      return crypto.createHmac('sha256', macKey).update(payload).digest('base64');
    },
  };
}

function snapStoreFiles() {
  const files = [KEY_FILE, REGISTRY_FILE, OAUTH_APPS_FILE];
  for (const name of fs.readdirSync(TOKENS_DIR)) {
    if (name.endsWith('.json')) files.push(path.join(TOKENS_DIR, name));
  }
  return new Map(files.map((file) => [file, fs.readFileSync(file)]));
}

test('sealStore refuses to start when no host seam is reachable', () => {
  store.setOAuthApp('sealed-oauth-app', {
    clientId: 'FAKE-SEALED-CLIENT-ID',
    clientSecret: 'FAKE-SEALED-CLIENT-SECRET',
  });
  store.saveApiKey(
    'sealed-second-token',
    { apiKey: 'FAKE-SEALED-SECOND-TOKEN' },
    { provider: 'sealed-second-token' }
  );
  const before = snapStoreFiles();
  store.setHostDecryptor(null);

  assert.throws(
    () => store.sealStore(),
    (error) => error && error.code === 'DEX_CM_SEALED_HOST_REQUIRED'
  );
  assert.equal(fs.existsSync(SEALED_MARKER), false);
  for (const [file, bytes] of before) assert.deepEqual(fs.readFileSync(file), bytes);
});

test('sealStore rolls back rewritten credentials and restores the legacy key when purge fails', () => {
  const before = snapStoreFiles();
  const mockHost = createMockHostDecryptor();
  store.setHostDecryptor(mockHost);
  const legacyKeyCustody = {
    capture() {
      return { fileKey: fs.readFileSync(KEY_FILE) };
    },
    purge() {
      fs.unlinkSync(KEY_FILE);
      throw new Error('injected keychain purge failure');
    },
    verifyPurged() {
      return false;
    },
    restore(snapshot) {
      fs.writeFileSync(KEY_FILE, snapshot.fileKey, { mode: 0o600 });
    },
  };

  assert.throws(
    () => store.sealStore({ legacyKeyCustody }),
    /injected keychain purge failure/
  );
  assert.equal(fs.existsSync(SEALED_MARKER), false);
  for (const [file, bytes] of before) assert.deepEqual(fs.readFileSync(file), bytes);
  assert.equal(
    store.loadToken('sealed-host-required').apiKey,
    'FAKE-SEALED-HOST-REQUIRED'
  );
});

test('sealStore seals every credential, writes the marker, and verifiably purges the legacy key', () => {
  const mockHost = createMockHostDecryptor();
  sealedHostAfterMigration = mockHost;
  store.setHostDecryptor(mockHost);

  const result = store.sealStore();

  assert.equal(result.sealed, true);
  assert.equal(result.tokenCount, 2);
  assert.equal(result.oauthAppCount, 1);
  assert.equal(fs.existsSync(KEY_FILE), false);
  assert.deepEqual(
    JSON.parse(fs.readFileSync(SEALED_MARKER, 'utf8')),
    { v: 1, custody: 'sealed-host' }
  );
  for (const file of fs.readdirSync(TOKENS_DIR).filter((name) => name.endsWith('.json'))) {
    assert.equal(
      JSON.parse(fs.readFileSync(path.join(TOKENS_DIR, file), 'utf8')).custody,
      'sealed-host'
    );
  }
  assert.equal(
    JSON.parse(fs.readFileSync(OAUTH_APPS_FILE, 'utf8'))['sealed-oauth-app'].clientSecret.custody,
    'sealed-host'
  );
  assert.equal(
    store.loadToken('sealed-host-required').apiKey,
    'FAKE-SEALED-HOST-REQUIRED'
  );
  assert.deepEqual(store.getOAuthApp('sealed-oauth-app'), {
    clientId: 'FAKE-SEALED-CLIENT-ID',
    clientSecret: 'FAKE-SEALED-CLIENT-SECRET',
  });
  assert.deepEqual(
    [...new Set(mockHost.calls.seal)].sort(),
    ['oauth-app:sealed-oauth-app', 'sealed-host-required', 'sealed-second-token']
  );
});

test('a Core process that ignores the marker still refuses sealed ciphertext after legacy-key purge', () => {
  store.setHostDecryptor(null);
  const tokenPath = path.join(TOKENS_DIR, 'sealed-host-required.json');
  const sealedBytes = fs.readFileSync(tokenPath);
  fs.renameSync(SEALED_MARKER, `${SEALED_MARKER}.held`);
  try {
    assert.throws(
      () => store.loadToken('sealed-host-required'),
      (error) => error && error.code === 'DEX_CM_KEY_LOST'
    );
    assert.equal(fs.existsSync(KEY_FILE), false);
  } finally {
    fs.writeFileSync(tokenPath, sealedBytes, { mode: 0o600 });
    for (const name of fs.readdirSync(TOKENS_DIR)) {
      if (name.startsWith('sealed-host-required.json.corrupt-')) {
        fs.rmSync(path.join(TOKENS_DIR, name));
      }
    }
    fs.renameSync(`${SEALED_MARKER}.held`, SEALED_MARKER);
  }
});

test('credential writes in a sealed store use the host seam and never recreate a Core key', () => {
  store.setHostDecryptor(sealedHostAfterMigration);

  store.saveApiKey(
    'sealed-new-write',
    { apiKey: 'FAKE-SEALED-NEW-WRITE' },
    { provider: 'sealed-new-write' }
  );

  assert.equal(fs.existsSync(KEY_FILE), false);
  assert.equal(
    JSON.parse(fs.readFileSync(path.join(TOKENS_DIR, 'sealed-new-write.json'), 'utf8')).custody,
    'sealed-host'
  );
  assert.equal(
    store.loadToken('sealed-new-write').apiKey,
    'FAKE-SEALED-NEW-WRITE'
  );
  store.deleteToken('sealed-new-write');
});

test('sealStore is a no-op when the store is already sealed', () => {
  store.setHostDecryptor(null);

  assert.deepEqual(store.sealStore(), {
    sealed: false,
    alreadySealed: true,
    tokenCount: 0,
    oauthAppCount: 0,
  });
  assert.equal(fs.existsSync(KEY_FILE), false);
});

test('key custody and status distinguish sealed-host without mislabelling standalone Core', () => {
  store.setHostDecryptor(sealedHostAfterMigration);
  assert.equal(store.keyCustodyMode(), 'sealed-host');

  fs.renameSync(SEALED_MARKER, `${SEALED_MARKER}.held`);
  try {
    assert.equal(store.keyCustodyMode(), 'file');
  } finally {
    fs.renameSync(`${SEALED_MARKER}.held`, SEALED_MARKER);
  }

  let jsonOutput = '';
  const originalWrite = process.stdout.write;
  process.stdout.write = (chunk) => {
    jsonOutput += String(chunk);
    return true;
  };
  try {
    cmdStatus({ json: 'true' });
  } finally {
    process.stdout.write = originalWrite;
  }
  const status = JSON.parse(jsonOutput);
  assert.equal(status.keyCustody, 'sealed-host');
  assert.ok(Array.isArray(status.connections));
  assert.ok(Object.prototype.hasOwnProperty.call(status, 'registryNotice'));

  const lines = [];
  const originalLog = console.log;
  console.log = (...args) => lines.push(args.join(' '));
  try {
    cmdStatus();
  } finally {
    console.log = originalLog;
  }
  assert.match(lines.join('\n'), /signed Dex desktop host/i);
});
