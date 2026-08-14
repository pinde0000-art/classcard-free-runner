const ALLOWED_ORIGIN = "https://pinde0000-art.github.io";
const OWNER = "pinde0000-art";
const REPO = "classcard-free-runner";

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin === ALLOWED_ORIGIN ? origin : ALLOWED_ORIGIN,
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Max-Age": "86400",
    "Content-Type": "application/json; charset=utf-8",
    "Vary": "Origin",
  };
}

function json(origin, body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: corsHeaders(origin),
  });
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
  if (!/^\d+$/.test(result.class_id) || !/^\d+$/.test(result.set_id)) {
    throw new Error("클래스 또는 세트 번호가 올바르지 않습니다.");
  }
  if (!Number.isInteger(result.start) || !Number.isInteger(result.end)
      || result.start < 1 || result.end < result.start || result.end > 1000) {
    throw new Error("카드 범위가 올바르지 않습니다.");
  }
  if (!Number.isInteger(result.card_count) || result.card_count < result.end
      || result.card_count > 1000) {
    throw new Error("전체 카드 수가 올바르지 않습니다.");
  }
  if (![1, 2, 3].includes(result.mode) || ![1, 2, 3, 4].includes(result.amount)) {
    throw new Error("학습 종류 또는 목표가 올바르지 않습니다.");
  }
  return Object.fromEntries(
    Object.entries(result).map(([key, value]) => [key, String(value)]),
  );
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }
    if (request.method !== "POST" || new URL(request.url).pathname !== "/run") {
      return json(origin, { ok: false, error: "Not found" }, 404);
    }
    if (origin !== ALLOWED_ORIGIN) {
      return json(origin, { ok: false, error: "허용되지 않은 사이트입니다." }, 403);
    }

    const authorization = request.headers.get("Authorization") || "";
    const suppliedKey = authorization.startsWith("Bearer ")
      ? authorization.slice(7)
      : "";
    if (!suppliedKey || !await secureEqual(suppliedKey, env.RUNNER_KEY)) {
      return json(origin, { ok: false, error: "실행 인증이 필요합니다." }, 401);
    }

    let inputs;
    try {
      inputs = validate(await request.json());
    } catch (error) {
      return json(origin, { ok: false, error: error.message }, 400);
    }

    const response = await fetch(
      `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/run.yml/dispatches`,
      {
        method: "POST",
        headers: {
          "Accept": "application/vnd.github+json",
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "Content-Type": "application/json",
          "User-Agent": "classcard-free-runner",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({ ref: "main", inputs }),
      },
    );
    if (!response.ok) {
      console.error(JSON.stringify({ event: "github_dispatch_failed", status: response.status }));
      return json(origin, { ok: false, error: "GitHub 실행 요청에 실패했습니다." }, 502);
    }
    console.log(JSON.stringify({ event: "workflow_dispatched", set_id: inputs.set_id }));
    return json(origin, { ok: true, message: "학습 실행을 시작했습니다." }, 202);
  },
};
