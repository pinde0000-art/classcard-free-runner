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
  if (![1, 2, 3].includes(result.mode) || ![1, 2, 3, 4].includes(result.amount)) throw new Error("학습 종류 또는 목표가 올바르지 않습니다.");
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

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(origin) });
    if (!["/run", "/status"].includes(url.pathname)) return json(origin, { ok: false, error: "Not found" }, 404);
    if (origin !== ALLOWED_ORIGIN) return json(origin, { ok: false, error: "허용되지 않은 사이트입니다." }, 403);

    const authorization = request.headers.get("Authorization") || "";
    const suppliedKey = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
    if (!suppliedKey || !await secureEqual(suppliedKey, env.RUNNER_KEY)) return json(origin, { ok: false, error: "실행 인증이 필요합니다." }, 401);

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
      if (state === "running" && !body.includes("CLASSCARD_PAYLOAD:") && age > 180000) state = "failed";
      return json(origin, {
        ok: true,
        issue: issue.number,
        progress: marker(body, "CLASSCARD_PROGRESS") || "0/0",
        state,
      });
    }
    if (request.method !== "POST" || url.pathname !== "/run") return json(origin, { ok: false, error: "Not found" }, 404);

    let inputs;
    try { inputs = validate(await request.json()); } catch (error) { return json(origin, { ok: false, error: error.message }, 400); }
    const requestId = crypto.randomUUID();
    inputs.request_id = requestId;
    const total = Number(inputs.end) - Number(inputs.start) + 1;
    const issueResponse = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/issues`, {
      method: "POST",
      headers: githubHeaders(env),
      body: JSON.stringify({
        title: `[Classcard status] ${requestId}`,
        body: `CLASSCARD_PROGRESS:0/${total}\nCLASSCARD_STATUS:running`,
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
