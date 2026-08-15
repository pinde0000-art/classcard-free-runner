import * as accounts from "./accounts.js";

const ALLOWED_ORIGIN = "https://pinde0000-art.github.io";
const OWNER = "pinde0000-art";
const REPO = "classcard-free-runner";

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin === ALLOWED_ORIGIN ? origin : ALLOWED_ORIGIN,
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Max-Age": "86400",
    "Content-Type": "application/json; charset=utf-8",
    "Vary": "Origin",
  };
}

function json(origin, body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: corsHeaders(origin) });
}

function githubHeaders(env, authenticate = true) {
  const headers = {
    "Accept": "application/vnd.github+json",
    "Content-Type": "application/json",
    "User-Agent": "classcard-free-runner",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  if (authenticate) headers.Authorization = `Bearer ${env.GITHUB_TOKEN}`;
  return headers;
}

async function secureEqual(left, right) {
  const encoder = new TextEncoder();
  const [leftHash, rightHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(left)),
    crypto.subtle.digest("SHA-256", encoder.encode(right)),
  ]);
  const a = new Uint8Array(leftHash);
  const b = new Uint8Array(rightHash);
  let difference = a.length ^ b.length;
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    difference |= (a[index] || 0) ^ (b[index] || 0);
  }
  return difference === 0;
}

function validate(payload) {
  const result = {
    class_id: String(payload.class_id || ""),
    set_id: String(payload.set_id || ""),
    title: String(payload.title || "Classcard").slice(0, 200),
    start: Number(payload.start),
    end: Number(payload.end),
    card_count: Number(payload.card_count),
    mode: Number(payload.mode),
    amount: Number(payload.amount),
  };
  if (!/^\d+$/.test(result.class_id) || !/^\d+$/.test(result.set_id)) throw new Error("클래스 또는 세트 번호가 올바르지 않습니다.");
  if (!Number.isInteger(result.start) || !Number.isInteger(result.end) || result.start < 1 || result.end < result.start || result.end > 1000) throw new Error("카드 범위가 올바르지 않습니다.");
  if (!Number.isInteger(result.card_count) || result.card_count < result.end || result.card_count > 1000) throw new Error("전체 카드 수가 올바르지 않습니다.");
  if (![1, 2, 3, 4].includes(result.mode) || ![1, 2, 3, 4].includes(result.amount)) throw new Error("학습 종류 또는 목표가 올바르지 않습니다.");
  if (result.mode === 4) {
    result.start = 1;
    result.end = result.card_count;
  }
  return Object.fromEntries(Object.entries(result).map(([key, value]) => [key, String(value)]));
}

function encodePayload(payload) {
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function marker(body, name) {
  return body.match(new RegExp(`^${name}:(.+)$`, "m"))?.[1]?.trim() || "";
}

const BROWSER_ROUTES = ["/run", "/status", "/account/link", "/account/relink", "/account/status", "/account/catalog", "/account/refresh", "/account/delete"];
const RUNNER_ROUTES = ["/runner/credentials", "/runner/profile"];

/** Classcard에 실제로 로그인되는 자격 증명인지만 확인한다. 비밀번호는 응답에 담지 않는다. */
async function verifyClasscardLogin(loginId, loginPassword) {
  const session = await fetch("https://www.classcard.net/Login", { redirect: "manual" });
  const cookie = (session.headers.get("set-cookie") || "").split(",").map((part) => part.split(";")[0].trim()).filter(Boolean).join("; ");
  const response = await fetch("https://www.classcard.net/LoginProc", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
      "Referer": "https://www.classcard.net/Login",
      "X-Requested-With": "XMLHttpRequest",
      ...(cookie ? { Cookie: cookie } : {}),
    },
    body: new URLSearchParams({ login_id: loginId, login_pwd: loginPassword }).toString(),
  });
  if (!response.ok) throw new Error("클래스카드에 연결하지 못했습니다.");
  let result;
  try { result = await response.json(); } catch { throw new Error("클래스카드 응답을 해석하지 못했습니다."); }
  if ((result || {}).result !== "ok") throw new Error("아이디 또는 비밀번호가 올바르지 않습니다.");
}

/** 계정 프로필과 카탈로그는 검증된 Selenium 워크플로가 채운다. 여기서는 실행만 요청한다. */
async function dispatchCatalogJob(env, accountId) {
  const response = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/catalog.yml/dispatches`, {
    method: "POST",
    headers: githubHeaders(env),
    body: JSON.stringify({ ref: "main", inputs: { account_id: accountId } }),
  });
  if (!response.ok) {
    console.error(JSON.stringify({ event: "catalog_dispatch_failed", status: response.status }));
    return false;
  }
  return true;
}

async function accountRoute(request, env, url, origin) {
  if (request.method !== "POST") return json(origin, { ok: false, error: "Not found" }, 404);
  if (!env.ACCOUNTS) return json(origin, { ok: false, error: "계정 저장소(KV)가 연결되지 않았습니다." }, 500);
  let body;
  try { body = await request.json(); } catch { return json(origin, { ok: false, error: "요청 형식이 올바르지 않습니다." }, 400); }

  try {
    if (url.pathname === "/account/link" || url.pathname === "/account/relink") {
      const loginId = String(body.login_id || "").trim();
      const loginPassword = String(body.login_pwd || "");
      if (!loginId || !loginPassword) return json(origin, { ok: false, error: "아이디와 비밀번호를 입력해 주세요." }, 400);

      let account = null;
      if (url.pathname === "/account/relink") {
        account = await accounts.authorizeAccount(env, String(body.account_id || ""), String(body.account_token || ""));
      }
      await verifyClasscardLogin(loginId, loginPassword);

      // 같은 클래스카드 아이디를 두 번 등록하지 않도록 아이디 해시로 대조한다(평문 저장 아님).
      const loginHash = await accounts.hashToken(`login:${loginId}`);
      if (!account) {
        const existing = await env.ACCOUNTS.list({ prefix: accounts.ACCOUNT_PREFIX });
        for (const entry of existing.keys) {
          const candidate = await env.ACCOUNTS.get(entry.name, "json");
          if (candidate && candidate.login_hash === loginHash) {
            return json(origin, { ok: false, error: "이미 등록된 계정입니다." }, 409);
          }
        }
      }

      const cipher = await accounts.sealCredentials(env, { id: loginId, pw: loginPassword });
      const accountToken = account ? null : accounts.randomToken();
      const record = account || {
        account_id: accounts.randomToken(16),
        nickname: "",
        avatar: "",
        linked_at: Date.now(),
        synced_at: 0,
        last_used_at: 0,
      };
      record.login_hash = loginHash;
      record.cipher = cipher;
      record.status = "syncing";
      record.error = "";
      if (accountToken) record.token_hash = await accounts.hashToken(accountToken);
      await accounts.writeAccount(env, record);

      const dispatched = await dispatchCatalogJob(env, record.account_id);
      if (!dispatched) {
        record.status = "error";
        record.error = "프로필을 불러오지 못했습니다. 새로고침해 주세요.";
        await accounts.writeAccount(env, record);
      }
      console.log(JSON.stringify({ event: "account_linked", account_id: record.account_id, login: accounts.maskAccountId(loginId) }));
      return json(origin, { ok: true, account: accounts.publicAccount(record), ...(accountToken ? { account_token: accountToken } : {}) }, 201);
    }

    const account = await accounts.authorizeAccount(env, String(body.account_id || ""), String(body.account_token || ""));

    if (url.pathname === "/account/status") {
      return json(origin, { ok: true, account: accounts.publicAccount(account) });
    }
    if (url.pathname === "/account/catalog") {
      const catalog = await env.ACCOUNTS.get(accounts.CATALOG_PREFIX + account.account_id, "json");
      return json(origin, { ok: true, account: accounts.publicAccount(account), catalog: catalog || { classes: [], synced_at: 0 } });
    }
    if (url.pathname === "/account/refresh") {
      account.status = "syncing";
      account.error = "";
      await accounts.writeAccount(env, account);
      const dispatched = await dispatchCatalogJob(env, account.account_id);
      if (!dispatched) return json(origin, { ok: false, error: "새로고침을 시작하지 못했습니다." }, 502);
      return json(origin, { ok: true, account: accounts.publicAccount(account) });
    }
    if (url.pathname === "/account/delete") {
      await accounts.purgeAccount(env, account.account_id);
      console.log(JSON.stringify({ event: "account_deleted", account_id: account.account_id }));
      return json(origin, { ok: true });
    }
    return json(origin, { ok: false, error: "Not found" }, 404);
  } catch (error) {
    if (error instanceof accounts.ConfigError) {
      console.error(JSON.stringify({ event: "account_config_error", message: error.message }));
      return json(origin, { ok: false, error: `설정 오류: ${error.message}` }, 500);
    }
    if (error instanceof accounts.AuthError) return json(origin, { ok: false, error: error.message }, 401);
    return json(origin, { ok: false, error: error.message || "계정 처리에 실패했습니다." }, 400);
  }
}

async function runnerRoute(request, env, url) {
  const headers = { "Content-Type": "application/json; charset=utf-8" };
  const reply = (payload, status = 200) => new Response(JSON.stringify(payload), { status, headers });
  if (request.method !== "POST") return reply({ ok: false, error: "Not found" }, 404);
  if (!env.ACCOUNTS) return reply({ ok: false, error: "계정 저장소(KV)가 연결되지 않았습니다." }, 500);

  const authorization = request.headers.get("Authorization") || "";
  const suppliedKey = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!suppliedKey || !await secureEqual(suppliedKey, env.RUNNER_KEY)) return reply({ ok: false, error: "인증이 필요합니다." }, 401);

  let body;
  try { body = await request.json(); } catch { return reply({ ok: false, error: "요청 형식이 올바르지 않습니다." }, 400); }

  try {
    if (url.pathname === "/runner/credentials") {
      // 일회용 인출: 같은 request_id로 두 번 받을 수 없다.
      const accountId = await accounts.redeemRunGrant(env, String(body.request_id || ""));
      const account = await accounts.readAccount(env, accountId);
      const credentials = await accounts.openCredentials(env, account.cipher);
      account.last_used_at = Date.now();
      await accounts.writeAccount(env, account);
      console.log(JSON.stringify({ event: "credentials_redeemed", account_id: accountId, login: accounts.maskAccountId(credentials.id) }));
      return reply({ ok: true, login_id: credentials.id, login_pwd: credentials.pw });
    }
    if (url.pathname === "/runner/profile") {
      // 카탈로그 워크플로가 닉네임/프로필/클래스 목록을 채워 넣는 경로.
      const account = await accounts.readAccount(env, String(body.account_id || ""));
      if (body.error) {
        account.status = String(body.status || "error");
        account.error = String(body.error).slice(0, 200);
      } else {
        account.nickname = String(body.nickname || account.nickname || "").slice(0, 80);
        account.avatar = String(body.avatar || "").slice(0, 500);
        account.status = "ready";
        account.error = "";
        account.synced_at = Date.now();
        await env.ACCOUNTS.put(
          accounts.CATALOG_PREFIX + account.account_id,
          JSON.stringify({ classes: body.classes || [], synced_at: account.synced_at })
        );
      }
      await accounts.writeAccount(env, account);
      return reply({ ok: true });
    }
    return reply({ ok: false, error: "Not found" }, 404);
  } catch (error) {
    if (error instanceof accounts.ConfigError) return reply({ ok: false, error: `설정 오류: ${error.message}` }, 500);
    if (error instanceof accounts.AuthError) return reply({ ok: false, error: error.message }, 401);
    return reply({ ok: false, error: "요청을 처리하지 못했습니다." }, 400);
  }
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(origin) });

    // GitHub Actions 러너 전용 경로: 브라우저 Origin 검사 대신 RUNNER_KEY만 요구한다.
    if (RUNNER_ROUTES.includes(url.pathname)) return runnerRoute(request, env, url);

    if (!BROWSER_ROUTES.includes(url.pathname)) return json(origin, { ok: false, error: "Not found" }, 404);
    if (origin !== ALLOWED_ORIGIN) return json(origin, { ok: false, error: "허용되지 않은 사이트입니다." }, 403);

    const authorization = request.headers.get("Authorization") || "";
    const suppliedKey = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
    if (!suppliedKey || !await secureEqual(suppliedKey, env.RUNNER_KEY)) return json(origin, { ok: false, error: "실행 인증이 필요합니다." }, 401);

    if (url.pathname.startsWith("/account/")) return accountRoute(request, env, url, origin);

    if (request.method === "GET" && url.pathname === "/status") {
      const requestId = url.searchParams.get("id") || "";
      if (!/^[a-f0-9-]{36}$/.test(requestId)) return json(origin, { ok: false, error: "잘못된 실행 번호입니다." }, 400);
      const knownIssue = Number(url.searchParams.get("issue"));
      let issue;
      if (Number.isInteger(knownIssue) && knownIssue > 0) {
        const response = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/issues/${knownIssue}`, { headers: githubHeaders(env) });
        if (response.ok) issue = await response.json();
      } else {
        const query = encodeURIComponent(`repo:${OWNER}/${REPO} in:title "[Classcard status] ${requestId}"`);
        const response = await fetch(`https://api.github.com/search/issues?q=${query}`, { headers: githubHeaders(env) });
        if (response.ok) {
          const result = await response.json();
          issue = (result.items || []).find((item) => item.title === `[Classcard status] ${requestId}`);
        }
      }
      if (!issue) return json(origin, { ok: true, progress: "0/0", state: "queued" });
      const body = issue.body || "";
      let state = marker(body, "CLASSCARD_STATUS") || (issue.state === "closed" ? "completed" : "queued");
      const age = Date.now() - Date.parse(issue.created_at || 0);
      if (["running", "preparing"].includes(state) && !body.includes("CLASSCARD_PAYLOAD:") && age > 180000) state = "failed";
      return json(origin, {
        ok: true,
        issue: issue.number,
        progress: marker(body, "CLASSCARD_PROGRESS") || "0/0",
        state,
      });
    }
    if (request.method !== "POST" || url.pathname !== "/run") return json(origin, { ok: false, error: "Not found" }, 404);

    let payload;
    try { payload = await request.json(); } catch { return json(origin, { ok: false, error: "요청 형식이 올바르지 않습니다." }, 400); }

    // 계정이 지정되면 그 계정으로 실행한다. 지정되지 않으면 기존 단일 계정
    // 방식(GitHub Secrets)을 그대로 쓰므로 이전 동작이 깨지지 않는다.
    let runAccount = null;
    if (payload.account_id) {
      if (!env.ACCOUNTS) return json(origin, { ok: false, error: "계정 저장소(KV)가 연결되지 않았습니다." }, 500);
      try {
        runAccount = await accounts.authorizeAccount(env, String(payload.account_id), String(payload.account_token || ""));
      } catch (error) {
        return json(origin, { ok: false, error: error.message }, 401);
      }
      if (runAccount.status !== "ready") return json(origin, { ok: false, error: "계정 정보를 아직 불러오는 중입니다." }, 409);
    }

    let inputs;
    try { inputs = validate(payload); } catch (error) { return json(origin, { ok: false, error: error.message }, 400); }
    const requestId = crypto.randomUUID();
    inputs.request_id = requestId;
    // 자격 증명은 입력값으로 넘기지 않는다(입력값은 Actions 로그에 평문으로 남는다).
    // 러너는 request_id로 일회용 인출만 할 수 있다.
    if (runAccount) {
      inputs.account_id = runAccount.account_id;
      await accounts.issueRunGrant(env, runAccount.account_id, requestId);
    }
    const total = Number(inputs.end) - Number(inputs.start) + 1;
    const issueResponse = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/issues`, {
      method: "POST",
      headers: githubHeaders(env),
      body: JSON.stringify({
        title: `[Classcard status] ${requestId}`,
        body: `CLASSCARD_PROGRESS:0/${total}\nCLASSCARD_STATUS:preparing`,
      }),
    });
    if (!issueResponse.ok) {
      console.error(JSON.stringify({ event: "github_progress_issue_failed", status: issueResponse.status }));
      return json(origin, { ok: false, error: "진행 기록을 만들지 못했습니다." }, 502);
    }
    const issue = await issueResponse.json();
    inputs.issue_number = String(issue.number);
    const response = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/run.yml/dispatches`, {
      method: "POST",
      headers: githubHeaders(env),
      body: JSON.stringify({ ref: "main", inputs }),
    });
    if (!response.ok) {
      console.error(JSON.stringify({ event: "github_issue_failed", status: response.status }));
      await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/issues/${issue.number}`, {
        method: "PATCH",
        headers: githubHeaders(env),
        body: JSON.stringify({ body: `CLASSCARD_PROGRESS:0/${total}\nCLASSCARD_STATUS:failed`, state: "closed" }),
      });
      return json(origin, { ok: false, error: "GitHub 실행을 시작하지 못했습니다." }, 502);
    }
    return json(origin, { ok: true, id: requestId, issue: issue.number, message: "학습을 시작했습니다." }, 202);
  },
};
