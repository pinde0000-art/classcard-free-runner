/**
 * Worker 계정 계층 로컬 테스트.
 *   node worker/test/accounts.test.mjs
 * Cloudflare 없이 KV를 메모리로 흉내 내고 Node WebCrypto로 실제 암복호화를 검증한다.
 */
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
if (!globalThis.btoa) globalThis.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
if (!globalThis.atob) globalThis.atob = (s) => Buffer.from(s, 'base64').toString('binary');

const accounts = await import('../src/accounts.js');

function makeKV() {
  const store = new Map();
  return {
    store,
    async get(key, type) { const v = store.get(key); if (v === undefined) return null; return type === 'json' ? JSON.parse(v) : v; },
    async put(key, value) { store.set(key, value); },
    async delete(key) { store.delete(key); },
    async list({ prefix = '' } = {}) { return { keys: [...store.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })) }; },
  };
}

const KEY_B64 = Buffer.from(webcrypto.getRandomValues(new Uint8Array(32))).toString('base64');
const env = { ACCOUNT_KEY: KEY_B64, ACCOUNTS: makeKV() };

let passed = 0;
async function test(name, fn) {
  try { await fn(); passed += 1; console.log(`  ok  ${name}`); }
  catch (error) { console.error(`  FAIL ${name}\n       ${error.message}`); process.exitCode = 1; }
}

console.log('AES-256-GCM 봉인');
await test('봉인 후 복호화하면 원래 자격 증명이 나온다', async () => {
  const sealed = await accounts.sealCredentials(env, { id: 'tester@example.com', pw: 'p@ssw0rd!' });
  const opened = await accounts.openCredentials(env, sealed);
  assert.equal(opened.id, 'tester@example.com');
  assert.equal(opened.pw, 'p@ssw0rd!');
});
await test('봉인 레코드에 평문이 남지 않는다', async () => {
  const sealed = await accounts.sealCredentials(env, { id: 'tester@example.com', pw: 'p@ssw0rd!' });
  const serialized = JSON.stringify(sealed);
  assert.ok(!serialized.includes('tester@example.com'), '아이디가 그대로 보임');
  assert.ok(!serialized.includes('p@ssw0rd!'), '비밀번호가 그대로 보임');
  assert.deepEqual(Object.keys(sealed).sort(), ['data', 'iv', 'tag', 'v']);
});
await test('같은 입력이라도 IV가 매번 달라 ciphertext가 달라진다', async () => {
  const a = await accounts.sealCredentials(env, { id: 'same', pw: 'same' });
  const b = await accounts.sealCredentials(env, { id: 'same', pw: 'same' });
  assert.notEqual(a.iv, b.iv);
  assert.notEqual(a.data, b.data);
});
await test('auth tag가 조작되면 복호화가 실패한다', async () => {
  const sealed = await accounts.sealCredentials(env, { id: 'a', pw: 'b' });
  const tampered = { ...sealed, tag: Buffer.from(webcrypto.getRandomValues(new Uint8Array(16))).toString('base64') };
  await assert.rejects(() => accounts.openCredentials(env, tampered), /재로그인/);
});
await test('ciphertext가 조작되면 복호화가 실패한다', async () => {
  const sealed = await accounts.sealCredentials(env, { id: 'a', pw: 'b' });
  const bytes = Buffer.from(sealed.data, 'base64'); bytes[0] ^= 0xff;
  await assert.rejects(() => accounts.openCredentials(env, { ...sealed, data: bytes.toString('base64') }), /재로그인/);
});
await test('다른 키로는 복호화되지 않는다', async () => {
  const sealed = await accounts.sealCredentials(env, { id: 'a', pw: 'b' });
  const otherEnv = { ACCOUNT_KEY: Buffer.from(webcrypto.getRandomValues(new Uint8Array(32))).toString('base64') };
  await assert.rejects(() => accounts.openCredentials(otherEnv, sealed), /재로그인/);
});
await test('버전이 다른 레코드는 사용하지 않고 재로그인을 요구한다', async () => {
  const sealed = await accounts.sealCredentials(env, { id: 'a', pw: 'b' });
  await assert.rejects(() => accounts.openCredentials(env, { ...sealed, v: 99 }), /재로그인/);
});
await test('과거 평문 레코드는 자동으로 쓰이지 않는다', async () => {
  await assert.rejects(() => accounts.openCredentials(env, { id: 'plain', pw: 'plain' }), /재로그인/);
});

console.log('키 설정 오류 시 평문 우회 금지');
await test('ACCOUNT_KEY가 없으면 설정 오류', async () => {
  await assert.rejects(() => accounts.sealCredentials({}, { id: 'a', pw: 'b' }), accounts.ConfigError);
});
await test('ACCOUNT_KEY 길이가 32바이트가 아니면 설정 오류', async () => {
  const short = { ACCOUNT_KEY: Buffer.alloc(16).toString('base64') };
  await assert.rejects(() => accounts.sealCredentials(short, { id: 'a', pw: 'b' }), accounts.ConfigError);
});
await test('ACCOUNT_KEY가 base64가 아니면 설정 오류', async () => {
  await assert.rejects(() => accounts.sealCredentials({ ACCOUNT_KEY: '!!!not base64!!!' }, { id: 'a', pw: 'b' }), accounts.ConfigError);
});

console.log('계정 토큰');
await test('올바른 토큰만 계정에 접근할 수 있다', async () => {
  const token = accounts.randomToken();
  const record = { account_id: accounts.randomToken(16), token_hash: await accounts.hashToken(token), status: 'ready' };
  await accounts.writeAccount(env, record);
  const authorized = await accounts.authorizeAccount(env, record.account_id, token);
  assert.equal(authorized.account_id, record.account_id);
  await assert.rejects(() => accounts.authorizeAccount(env, record.account_id, accounts.randomToken()), accounts.AuthError);
});
await test('토큰 해시만 저장되고 원본 토큰은 저장되지 않는다', async () => {
  const token = accounts.randomToken();
  const record = { account_id: accounts.randomToken(16), token_hash: await accounts.hashToken(token) };
  await accounts.writeAccount(env, record);
  const stored = await env.ACCOUNTS.get(accounts.ACCOUNT_PREFIX + record.account_id);
  assert.ok(!stored.includes(token), '토큰 원본이 저장됨');
});
await test('공개 표현에는 자격 증명과 토큰 해시가 없다', async () => {
  const record = { account_id: 'x'.repeat(20), nickname: '홍길동', cipher: { v: 1, iv: 'i', data: 'd', tag: 't' }, token_hash: 'h', login_hash: 'l', status: 'ready' };
  const view = JSON.stringify(accounts.publicAccount(record));
  assert.ok(!view.includes('cipher') && !view.includes('token_hash') && !view.includes('login_hash'));
  assert.ok(view.includes('홍길동'));
});

console.log('일회용 실행 인출권');
await test('발급한 request_id로 한 번만 인출된다', async () => {
  const accountId = accounts.randomToken(16);
  const requestId = webcrypto.randomUUID();
  await accounts.issueRunGrant(env, accountId, requestId);
  assert.equal(await accounts.redeemRunGrant(env, requestId), accountId);
  await assert.rejects(() => accounts.redeemRunGrant(env, requestId), /만료|사용/);
});
await test('발급되지 않은 request_id로는 인출할 수 없다', async () => {
  await assert.rejects(() => accounts.redeemRunGrant(env, webcrypto.randomUUID()), accounts.AuthError);
});
await test('형식이 틀린 request_id는 거부한다', async () => {
  await assert.rejects(() => accounts.redeemRunGrant(env, 'not-a-uuid'), accounts.AuthError);
});

console.log('계정 삭제');
await test('삭제하면 암호문·카탈로그·대기 중 인출권이 모두 사라진다', async () => {
  const token = accounts.randomToken();
  const accountId = accounts.randomToken(16);
  const requestId = webcrypto.randomUUID();
  await accounts.writeAccount(env, { account_id: accountId, token_hash: await accounts.hashToken(token), cipher: await accounts.sealCredentials(env, { id: 'a', pw: 'b' }) });
  await env.ACCOUNTS.put(accounts.CATALOG_PREFIX + accountId, JSON.stringify({ classes: [] }));
  await accounts.issueRunGrant(env, accountId, requestId);
  await accounts.purgeAccount(env, accountId);
  assert.equal(await env.ACCOUNTS.get(accounts.ACCOUNT_PREFIX + accountId), null);
  assert.equal(await env.ACCOUNTS.get(accounts.CATALOG_PREFIX + accountId), null);
  assert.equal(await env.ACCOUNTS.get(accounts.RUN_PREFIX + requestId), null);
});
await test('다른 계정의 인출권은 삭제 시 남아 있다', async () => {
  const keep = accounts.randomToken(16), drop = accounts.randomToken(16);
  const keepRequest = webcrypto.randomUUID(), dropRequest = webcrypto.randomUUID();
  await accounts.issueRunGrant(env, keep, keepRequest);
  await accounts.issueRunGrant(env, drop, dropRequest);
  await accounts.purgeAccount(env, drop);
  assert.ok(await env.ACCOUNTS.get(accounts.RUN_PREFIX + keepRequest));
  assert.equal(await env.ACCOUNTS.get(accounts.RUN_PREFIX + dropRequest), null);
});

console.log('계정 격리');
await test('계정마다 다른 카탈로그를 보관한다', async () => {
  const a = accounts.randomToken(16), b = accounts.randomToken(16);
  await env.ACCOUNTS.put(accounts.CATALOG_PREFIX + a, JSON.stringify({ classes: [{ id: '1', name: 'A반' }] }));
  await env.ACCOUNTS.put(accounts.CATALOG_PREFIX + b, JSON.stringify({ classes: [{ id: '2', name: 'B반' }] }));
  const ca = await env.ACCOUNTS.get(accounts.CATALOG_PREFIX + a, 'json');
  const cb = await env.ACCOUNTS.get(accounts.CATALOG_PREFIX + b, 'json');
  assert.equal(ca.classes[0].name, 'A반');
  assert.equal(cb.classes[0].name, 'B반');
});

console.log('로그 마스킹');
await test('아이디는 마스킹되어 기록된다', () => {
  assert.equal(accounts.maskAccountId('pinde0000'), 'pi*******');
  assert.ok(!accounts.maskAccountId('pinde0000').includes('nde0000'));
});

console.log(`\n통과: ${passed}`);
