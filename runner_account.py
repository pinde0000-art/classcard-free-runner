"""GitHub Actions 러너에서 Worker와 주고받는 계정 연동 helper.

원칙:
- 자격 증명은 일회용 인출권(request_id/sync_id)으로만 받아온다.
- 받은 아이디/비밀번호는 절대 출력하지 않는다. 로그에는 마스킹한 값만 남긴다.
- 러너 디스크에 계정 설정을 저장하지 않도록 CLASSCARD_EPHEMERAL을 켠다.
"""

import json
import os
import urllib.error
import urllib.request


def mask_login(value):
    text = str(value or "")
    if len(text) <= 2:
        return "**"
    return f"{text[:2]}{'*' * max(2, len(text) - 2)}"


def _worker_url(path):
    base = os.environ.get("WORKER_URL", "").strip().rstrip("/")
    if not base:
        raise RuntimeError("WORKER_URL이 설정되지 않았습니다.")
    if not base.startswith("https://"):
        raise RuntimeError("WORKER_URL은 https여야 합니다.")
    return base + path


def _post(path, payload):
    key = os.environ.get("RUNNER_KEY", "").strip()
    if not key:
        raise RuntimeError("RUNNER_KEY가 설정되지 않았습니다.")
    request = urllib.request.Request(
        _worker_url(path),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "classcard-free-runner",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error", "")
        except Exception:
            pass
        raise RuntimeError(f"Worker 요청 실패({error.code}): {detail}") from None


def load_credentials(grant_id):
    """일회용 인출권으로 자격 증명을 받아 환경변수에만 넣는다."""
    data = _post("/runner/credentials", {"request_id": grant_id})
    if not data.get("ok"):
        raise RuntimeError("자격 증명을 받지 못했습니다.")
    login_id = data.get("login_id") or ""
    login_pwd = data.get("login_pwd") or ""
    if not login_id or not login_pwd:
        raise RuntimeError("자격 증명이 비어 있습니다.")
    os.environ["CLASSCARD_ID"] = login_id
    os.environ["CLASSCARD_PASSWORD"] = login_pwd
    os.environ["CLASSCARD_EPHEMERAL"] = "1"
    print(f"계정 자격 증명을 받았습니다: {mask_login(login_id)}", flush=True)
    return login_id


def report_profile(account_id, nickname="", avatar="", classes=None, error="", status=""):
    payload = {"account_id": account_id}
    if error:
        payload["error"] = error
        payload["status"] = status or "error"
    else:
        payload["nickname"] = nickname
        payload["avatar"] = avatar
        payload["classes"] = classes or []
    return _post("/runner/profile", payload)
