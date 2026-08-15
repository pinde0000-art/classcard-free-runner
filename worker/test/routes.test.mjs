/**
 * Worker 라우팅 통합 테스트 (Cloudflare 없이).
 *   node worker/test/routes.test.mjs
 * GitHub/Classcard 호출은 가로채고, 응답에 자격 증명이 새는지와
 * 기존 단일 계정 실행 경로가 그대로인지 확인한다.
 */
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
if (!globalThis.btoa) globalThis.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
if (!globalThis.atob) globalThis.atob = (s) => Buffer.from(s, 'base64').toString('binary');

const ORIGIN = 'https://pinde0000-art.github.io';
const RUNNER_KEY = 'test-runner-key';
const accounts = await import('../src/accounts.js');

let calls = [];
globalThis.fetch = async (url, options = {}) => {
  const target = String(url);
  calls.push({ url: target, options });
  if (target.includes('/LoginProc')) {
    const body = String(options.body || '');
    const ok = body.includes('login_pwd=right-password');
    return new Response(JSON.stringify({ result: ok ? 'ok' : 'fail' }), { status: 200 });
  }
  if (target.includes('classcard.net/Login')) return new Response('', { status: 200, headers: { 'set-cookie': 'SESS=abc; Path=/' } });
  if (target.includes('/issues') && options.method === 'POST') return new Response(JSON.stringify({ number: 42 }), { status: 201 });
  if (target.includes('/dispatches')) return new Response(null, { status: 204 });
  return new Response('{}', { status: 200 });
};

function makeKV() {
  const store = new Map();
  return {
    store,
    async get(k, t) { const v = store.get(k); if (v === undefined) return null; return t === 'json' ? JSON.parse(v) : v; },
    async put(k, v) { store.set(k, v); },
    async delete(k) { store.delete(k); },
    async list({ prefix = '' } = {}) { return { keys: [...store.keys()].filter((x) => x.startsWith(prefix)).map((name) => ({ name })) }; },
  };
}

const worker = (await import('../src/index.js')).default;
const KEY_B64 = Buffer.from(webcrypto.getRandomValues(new Uint8Array(32))).toString('base64');
const baseEnv = () => ({ RUNNER_KEY, GITHUB_TOKEN: 'gh-token', ACCOUNT_KEY: KEY_B64, ACCOUNTS: makeKV() });

const post = (path, body, { key = RUNNER_KEY, origin = ORIGIN } = {}) =>
  new Request(`https://worker.example${path}`, {
    method: 'POST',
    headers: { Origin: origin, Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

const RUN_PAYLOAD = { class_id: '2059431', set_id: '10513878', title: 'T', start: 1, end: 11, card_count: 11, mode: 4, amount: 1 };

let passed = 0;
async function test(name, fn) {
  calls = [];
  try { await fn(); passed += 1; console.log(`  ok  ${name}`); }
  catch (error) { console.error(`  FAIL ${name}\n       ${error.stack.split('\n').slice(0, 3).join('\n       ')}`); process.exitCode = 1; }
}

console.log('기존 동작 보존');
await test('계정을 지정하지 않으면 예전처럼 실행된다', async () => {
  const env = baseEnv();
  const response = await worker.fetch(post('/run', RUN_PAYLOAD), env);
  assert.equal(response.status, 202);
  const data = await response.json();
  assert.equal(data.ok, true);
  assert.equal(data.issue, 42);
  const dispatch = calls.find((c) => c.url.includes('/run.yml/dispatches'));
  const inputs = JSON.parse(dispatch.options.body).inputs;
  assert.ok(!('account_id' in inputs), '계정 없이도 account_id가 들어감');
  assert.equal(inputs.mode, '4');
  assert.equal(inputs.start, '1');
  assert.equal(inputs.end, '11');
});
await test('테스트 모드는 여전히 전체 카드 범위로 강제된다', async () => {
  const env = baseEnv();
  await worker.fetch(post('/run', { ...RUN_PAYLOAD, start: 3, end: 5 }), env);
  const inputs = JSON.parse(calls.find((c) => c.url.includes('/run.yml/dispatches')).options.body).inputs;
  assert.equal(inputs.start, '1');
  assert.equal(inputs.end, '11');
});
await test('허용되지 않은 Origin은 여전히 막힌다', async () => {
  const response = await worker.fetch(post('/run', RUN_PAYLOAD, { origin: 'https://evil.example' }), baseEnv());
  assert.equal(response.status, 403);
});
await test('잘못된 RUNNER_KEY는 여전히 막힌다', async () => {
  const response = await worker.fetch(post('/run', RUN_PAYLOAD, { key: 'wrong' }), baseEnv());
  assert.equal(response.status, 401);
});

console.log('계정 연결');
await test('로그인 실패 시 계정이 만들어지지 않는다', async () => {
  const env = baseEnv();
  const response = await worker.fetch(post('/account/link', { login_id: 'user@example.com', login_pwd: 'wrong-password' }), env);
  assert.equal(response.status, 400);
  const data = await response.json();
  assert.match(data.error, /아이디 또는 비밀번호/);
  assert.equal(env.ACCOUNTS.store.size, 0);
});
await test('로그인 성공 시 account_token을 발급하고 비밀번호는 돌려주지 않는다', async () => {
  const env = baseEnv();
  const response = await worker.fetch(post('/account/link', { login_id: 'user@example.com', login_pwd: 'right-password' }), env);
  assert.equal(response.status, 201);
  const raw = JSON.stringify(await response.json());
  assert.ok(raw.includes('account_token'), '토큰 미발급');
  assert.ok(!raw.includes('right-password'), '비밀번호가 응답에 포함됨');
  assert.ok(!raw.includes('user@example.com'), '아이디가 응답에 포함됨');
});
await test('KV에 평문 아이디·비밀번호가 저장되지 않는다', async () => {
  const env = baseEnv();
  await worker.fetch(post('/account/link', { login_id: 'user@example.com', login_pwd: 'right-password' }), env);
  const dump = [...env.ACCOUNTS.store.values()].join('\n');
  assert.ok(!dump.includes('right-password'), 'KV에 비밀번호 평문 저장됨');
  assert.ok(!dump.includes('user@example.com'), 'KV에 아이디 평문 저장됨');
  assert.ok(dump.includes('"v":1') && dump.includes('"tag"'), '봉인 형식이 아님');
});
await test('같은 계정을 두 번 등록하면 거부한다', async () => {
  const env = baseEnv();
  await worker.fetch(post('/account/link', { login_id: 'dup@example.com', login_pwd: 'right-password' }), env);
  const second = await worker.fetch(post('/account/link', { login_id: 'dup@example.com', login_pwd: 'right-password' }), env);
  assert.equal(second.status, 409);
});
await test('ACCOUNT_KEY가 없으면 평문 저장으로 넘어가지 않고 설정 오류', async () => {
  const env = { ...baseEnv(), ACCOUNT_KEY: undefined };
  const response = await worker.fetch(post('/account/link', { login_id: 'a@b.c', login_pwd: 'right-password' }), env);
  assert.equal(response.status, 500);
  assert.match((await response.json()).error, /설정 오류/);
  assert.equal(env.ACCOUNTS.store.size, 0);
});
await test('KV 바인딩이 없으면 계정 기능이 막힌다', async () => {
  const env = { ...baseEnv(), ACCOUNTS: undefined };
  const response = await worker.fetch(post('/account/link', { login_id: 'a@b.c', login_pwd: 'right-password' }), env);
  assert.equal(response.status, 500);
});

console.log('계정별 실행과 격리');
async function link(env, id) {
  const response = await worker.fetch(post('/account/link', { login_id: id, login_pwd: 'right-password' }), env);
  const data = await response.json();
  const record = await env.ACCOUNTS.get(accounts.ACCOUNT_PREFIX + data.account.account_id, 'json');
  record.status = 'ready';
  await env.ACCOUNTS.put(accounts.ACCOUNT_PREFIX + record.account_id, JSON.stringify(record));
  return { id: data.account.account_id, token: data.account_token };
}
await test('다른 계정의 토큰으로는 접근할 수 없다', async () => {
  const env = baseEnv();
  const a = await link(env, 'a@example.com');
  const b = await link(env, 'b@example.com');
  const response = await worker.fetch(post('/account/status', { account_id: a.id, account_token: b.token }), env);
  assert.equal(response.status, 401);
});
await test('계정 실행은 자격 증명 대신 account_id만 입력으로 넘긴다', async () => {
  const env = baseEnv();
  const a = await link(env, 'a@example.com');
  const response = await worker.fetch(post('/run', { ...RUN_PAYLOAD, account_id: a.id, account_token: a.token }), env);
  assert.equal(response.status, 202);
  const dispatch = calls.find((c) => c.url.includes('/run.yml/dispatches'));
  const body = dispatch.options.body;
  assert.ok(body.includes(a.id), 'account_id가 입력에 없음');
  assert.ok(!body.includes('right-password'), '입력에 비밀번호가 들어감');
  assert.ok(!body.includes('a@example.com'), '입력에 아이디가 들어감');
});
await test('러너는 request_id로 자격 증명을 한 번만 인출한다', async () => {
  const env = baseEnv();
  const a = await link(env, 'a@example.com');
  const runResponse = await worker.fetch(post('/run', { ...RUN_PAYLOAD, account_id: a.id, account_token: a.token }), env);
  const requestId = (await runResponse.json()).id;
  const first = await worker.fetch(new Request('https://worker.example/runner/credentials', {
    method: 'POST', headers: { Authorization: `Bearer ${RUNNER_KEY}` }, body: JSON.stringify({ request_id: requestId }),
  }), env);
  const credentials = await first.json();
  assert.equal(credentials.login_id, 'a@example.com');
  assert.equal(credentials.login_pwd, 'right-password');
  const second = await worker.fetch(new Request('https://worker.example/runner/credentials', {
    method: 'POST', headers: { Authorization: `Bearer ${RUNNER_KEY}` }, body: JSON.stringify({ request_id: requestId }),
  }), env);
  assert.equal(second.status, 401);
});
await test('RUNNER_KEY 없이는 자격 증명을 인출할 수 없다', async () => {
  const env = baseEnv();
  const a = await link(env, 'a@example.com');
  const runResponse = await worker.fetch(post('/run', { ...RUN_PAYLOAD, account_id: a.id, account_token: a.token }), env);
  const requestId = (await runResponse.json()).id;
  const response = await worker.fetch(new Request('https://worker.example/runner/credentials', {
    method: 'POST', headers: { Authorization: 'Bearer wrong' }, body: JSON.stringify({ request_id: requestId }),
  }), env);
  assert.equal(response.status, 401);
});
await test('브라우저는 러너 전용 경로를 쓸 수 없다', async () => {
  const env = baseEnv();
  const response = await worker.fetch(post('/runner/credentials', { request_id: webcrypto.randomUUID() }, { key: RUNNER_KEY }), env);
  assert.equal(response.status, 401); // 인출권이 없으므로 거부
});
await test('아직 동기화 중인 계정으로는 실행할 수 없다', async () => {
  const env = baseEnv();
  const response = await worker.fetch(post('/account/link', { login_id: 'sync@example.com', login_pwd: 'right-password' }), env);
  const data = await response.json();
  const run = await worker.fetch(post('/run', { ...RUN_PAYLOAD, account_id: data.account.account_id, account_token: data.account_token }), env);
  assert.equal(run.status, 409);
});
await test('계정을 삭제하면 저장된 모든 흔적이 사라진다', async () => {
  const env = baseEnv();
  const a = await link(env, 'a@example.com');
  await env.ACCOUNTS.put(accounts.CATALOG_PREFIX + a.id, JSON.stringify({ classes: [{ id: '1' }] }));
  const response = await worker.fetch(post('/account/delete', { account_id: a.id, account_token: a.token }), env);
  assert.equal(response.status, 200);
  assert.equal(await env.ACCOUNTS.get(accounts.ACCOUNT_PREFIX + a.id), null);
  assert.equal(await env.ACCOUNTS.get(accounts.CATALOG_PREFIX + a.id), null);
});

console.log('카탈로그 격리');
await test('계정마다 자기 클래스 목록만 돌려준다', async () => {
  const env = baseEnv();
  const a = await link(env, 'a@example.com');
  const b = await link(env, 'b@example.com');
  await worker.fetch(new Request('https://worker.example/runner/profile', {
    method: 'POST', headers: { Authorization: `Bearer ${RUNNER_KEY}` },
    body: JSON.stringify({ account_id: a.id, nickname: 'A선생', classes: [{ id: '1', name: 'A반', sets: [] }] }),
  }), env);
  await worker.fetch(new Request('https://worker.example/runner/profile', {
    method: 'POST', headers: { Authorization: `Bearer ${RUNNER_KEY}` },
    body: JSON.stringify({ account_id: b.id, nickname: 'B선생', classes: [{ id: '2', name: 'B반', sets: [] }] }),
  }), env);
  const catalogA = await (await worker.fetch(post('/account/catalog', { account_id: a.id, account_token: a.token }), env)).json();
  const catalogB = await (await worker.fetch(post('/account/catalog', { account_id: b.id, account_token: b.token }), env)).json();
  assert.equal(catalogA.catalog.classes[0].name, 'A반');
  assert.equal(catalogB.catalog.classes[0].name, 'B반');
  assert.equal(catalogA.account.nickname, 'A선생');
  assert.equal(catalogB.account.nickname, 'B선생');
});

console.log(`\n통과: ${passed}`);
