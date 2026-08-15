/**
 * 계정 자격 증명 보관 계층.
 *
 * 설계 규칙(우회 금지):
 * - 평문 아이디/비밀번호는 KV에 절대 저장하지 않는다. AES-256-GCM 봉인만 저장한다.
 * - 암호화 키는 Worker Secret(ACCOUNT_KEY)에서만 읽는다. KV나 소스에는 두지 않는다.
 * - 키가 없거나 형식이 틀리면 평문으로 대체하지 않고 설정 오류를 던진다.
 * - 봉인마다 새 무작위 IV를 쓰고, ciphertext와 auth tag를 분리해 저장한다.
 * - 봉인 레코드에 버전(v)을 남겨 나중에 키 교체를 할 수 있게 한다.
 */

export const ENC_VERSION = 1;
export const ACCOUNT_PREFIX = 'acct:';
export const CATALOG_PREFIX = 'cat:';
export const RUN_PREFIX = 'run:';
export const RUN_TTL_SECONDS = 900;
export const MAX_DEVICE_TOKENS = 10;

export class ConfigError extends Error {}
export class AuthError extends Error {}

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function toBase64(bytes) {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function fromBase64(text) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

/** ACCOUNT_KEY(base64 32바이트)를 AES-GCM 키로 가져온다. 없으면 설정 오류. */
export async function loadAccountKey(env) {
  const raw = env && env.ACCOUNT_KEY;
  if (!raw) throw new ConfigError('ACCOUNT_KEY 시크릿이 설정되지 않았습니다.');
  let bytes;
  try {
    bytes = fromBase64(String(raw).trim());
  } catch {
    throw new ConfigError('ACCOUNT_KEY가 base64 형식이 아닙니다.');
  }
  if (bytes.length !== 32) throw new ConfigError('ACCOUNT_KEY는 base64로 인코딩한 32바이트여야 합니다.');
  return crypto.subtle.importKey('raw', bytes, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
}

/** {id, pw}를 봉인한다. 반환값에는 평문이 남지 않는다. */
export async function sealCredentials(env, credentials) {
  const key = await loadAccountKey(env);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const sealed = new Uint8Array(
    await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, encoder.encode(JSON.stringify(credentials)))
  );
  // WebCrypto는 ciphertext 뒤에 16바이트 auth tag를 붙여서 돌려준다. 요청대로 분리 보관한다.
  const split = sealed.length - 16;
  return {
    v: ENC_VERSION,
    iv: toBase64(iv),
    data: toBase64(sealed.slice(0, split)),
    tag: toBase64(sealed.slice(split)),
  };
}

/** 봉인된 자격 증명을 복호화한다. 버전이 다르면 사용하지 않고 재로그인을 요구한다. */
export async function openCredentials(env, sealedRecord) {
  if (!sealedRecord || typeof sealedRecord !== 'object') throw new AuthError('저장된 자격 증명이 없습니다.');
  if (sealedRecord.v !== ENC_VERSION) throw new AuthError('저장 형식이 달라 재로그인이 필요합니다.');
  if (!sealedRecord.iv || !sealedRecord.data || !sealedRecord.tag) {
    throw new AuthError('저장된 자격 증명이 손상되었습니다. 재로그인이 필요합니다.');
  }
  const key = await loadAccountKey(env);
  const iv = fromBase64(sealedRecord.iv);
  const data = fromBase64(sealedRecord.data);
  const tag = fromBase64(sealedRecord.tag);
  const combined = new Uint8Array(data.length + tag.length);
  combined.set(data, 0);
  combined.set(tag, data.length);
  let plain;
  try {
    plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, combined);
  } catch {
    throw new AuthError('자격 증명을 복호화하지 못했습니다. 재로그인이 필요합니다.');
  }
  return JSON.parse(decoder.decode(plain));
}

export function randomToken(byteLength = 32) {
  const bytes = crypto.getRandomValues(new Uint8Array(byteLength));
  return toBase64(bytes).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export async function hashToken(token) {
  const digest = await crypto.subtle.digest('SHA-256', encoder.encode(String(token)));
  return toBase64(new Uint8Array(digest));
}

/** 타이밍 차이를 줄이기 위해 해시를 비교한다. */
export async function tokenMatches(token, storedHash) {
  if (!token || !storedHash) return false;
  const candidate = await hashToken(token);
  if (candidate.length !== storedHash.length) return false;
  let difference = 0;
  for (let index = 0; index < candidate.length; index += 1) {
    difference |= candidate.charCodeAt(index) ^ storedHash.charCodeAt(index);
  }
  return difference === 0;
}

/** 기존 단일 token_hash 레코드도 읽으면서 기기별 토큰 목록으로 점진 이전한다. */
export function accountTokenHashes(account) {
  const hashes = Array.isArray(account.token_hashes) ? account.token_hashes.filter(Boolean) : [];
  if (account.token_hash && !hashes.includes(account.token_hash)) hashes.push(account.token_hash);
  return hashes;
}

/** 새 기기 토큰을 추가하고 오래된 토큰은 제한 개수까지만 유지한다. */
export async function addAccountToken(account, token) {
  const hashes = accountTokenHashes(account);
  hashes.push(await hashToken(token));
  account.token_hashes = [...new Set(hashes)].slice(-MAX_DEVICE_TOKENS);
  delete account.token_hash;
  return account;
}

/** 로그에 남기면 안 되는 값을 가린다. */
export function maskAccountId(accountLoginId) {
  const text = String(accountLoginId || '');
  if (text.length <= 2) return '**';
  return `${text.slice(0, 2)}${'*'.repeat(Math.max(2, text.length - 2))}`;
}

export async function readAccount(env, accountId) {
  if (!accountId || !/^[A-Za-z0-9_-]{16,64}$/.test(accountId)) throw new AuthError('계정 식별자가 올바르지 않습니다.');
  const stored = await env.ACCOUNTS.get(ACCOUNT_PREFIX + accountId, 'json');
  if (!stored) throw new AuthError('등록되지 않은 계정입니다.');
  return stored;
}

/** 기기에 보관된 account_token으로 계정 접근 권한을 확인한다. */
export async function authorizeAccount(env, accountId, accountToken) {
  const account = await readAccount(env, accountId);
  const matches = await Promise.all(accountTokenHashes(account).map((storedHash) => tokenMatches(accountToken, storedHash)));
  if (!matches.some(Boolean)) throw new AuthError('계정 접근 권한이 없습니다.');
  return account;
}

export function publicAccount(account) {
  // 절대 자격 증명(cipher)을 포함하지 않는 표현.
  return {
    account_id: account.account_id,
    nickname: account.nickname || '',
    avatar: account.avatar || '',
    status: account.status || 'syncing',
    error: account.error || '',
    linked_at: account.linked_at || 0,
    synced_at: account.synced_at || 0,
    last_used_at: account.last_used_at || 0,
  };
}

export async function writeAccount(env, account) {
  account.updated_at = Date.now();
  await env.ACCOUNTS.put(ACCOUNT_PREFIX + account.account_id, JSON.stringify(account));
  return account;
}

/** 계정과 함께 카탈로그 캐시, 대기 중인 실행 토큰까지 지운다. */
export async function purgeAccount(env, accountId) {
  await env.ACCOUNTS.delete(ACCOUNT_PREFIX + accountId);
  await env.ACCOUNTS.delete(CATALOG_PREFIX + accountId);
  const pending = await env.ACCOUNTS.list({ prefix: RUN_PREFIX });
  for (const entry of pending.keys) {
    const mapping = await env.ACCOUNTS.get(entry.name, 'json');
    if (mapping && mapping.account_id === accountId) await env.ACCOUNTS.delete(entry.name);
  }
}

/**
 * 실행 요청 하나에만 쓰이는 일회용 자격 증명 인출권을 만든다.
 * 별도의 토큰 문자열을 workflow 입력으로 넘기지 않는다 - 입력값은 Actions 로그에
 * 평문으로 찍히기 때문이다. 대신 이미 입력으로 들어가는 request_id에 매핑하고,
 * 실제 인출은 GitHub Secret(RUNNER_KEY)을 아는 러너만 할 수 있게 한다.
 */
export async function issueRunGrant(env, accountId, requestId) {
  await env.ACCOUNTS.put(
    RUN_PREFIX + requestId,
    JSON.stringify({ account_id: accountId, issued_at: Date.now() }),
    { expirationTtl: RUN_TTL_SECONDS }
  );
}

/** 일회용: 읽는 즉시 삭제한다. 같은 request_id로 두 번 인출할 수 없다. */
export async function redeemRunGrant(env, requestId) {
  if (!requestId || !/^[a-f0-9-]{36}$/.test(requestId)) throw new AuthError('실행 번호가 올바르지 않습니다.');
  const mapping = await env.ACCOUNTS.get(RUN_PREFIX + requestId, 'json');
  if (!mapping) throw new AuthError('실행 토큰이 만료되었거나 이미 사용되었습니다.');
  await env.ACCOUNTS.delete(RUN_PREFIX + requestId);
  return mapping.account_id;
}
