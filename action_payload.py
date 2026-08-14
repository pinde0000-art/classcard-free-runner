import base64
import json
import os
import re
import subprocess
import sys
import urllib.request


def load_payload():
    if os.environ.get("EVENT_NAME") == "issues":
        body = os.environ.get("ISSUE_BODY", "")
        match = re.search(r"CLASSCARD_PAYLOAD:([A-Za-z0-9_-]+)", body)
        if not match:
            raise RuntimeError("실행 요청에서 자동화 설정을 찾지 못했습니다.")
        raw = base64.urlsafe_b64decode(match.group(1) + "===")
        return json.loads(raw.decode("utf-8"))
    return {
        "class_id": os.environ["INPUT_CLASS_ID"],
        "set_id": os.environ["INPUT_SET_ID"],
        "title": os.environ.get("INPUT_TITLE", ""),
        "start": int(os.environ["INPUT_START"]),
        "end": int(os.environ["INPUT_END"]),
        "card_count": int(os.environ["INPUT_CARD_COUNT"]),
        "mode": int(os.environ["INPUT_MODE"]),
        "amount": int(os.environ["INPUT_AMOUNT"]),
    }


payload = load_payload()
encoded = base64.urlsafe_b64encode(
    json.dumps(payload, ensure_ascii=False).encode("utf-8")
).decode("ascii")


def github_request(path, data):
    token = os.environ.get("GH_PROGRESS_TOKEN")
    repository = os.environ.get("GH_REPOSITORY")
    if not token or not repository:
        return None
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}{path}",
        data=json.dumps(data).encode("utf-8"),
        method="POST" if path == "/issues" else "PATCH",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "classcard-free-runner",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    return json.loads(urllib.request.urlopen(request, timeout=10).read())


def ensure_progress_issue(total):
    existing = os.environ.get("ISSUE_NUMBER")
    if existing:
        return existing
    request_id = os.environ.get("INPUT_REQUEST_ID")
    if not request_id:
        return ""
    try:
        issue = github_request(
            "/issues",
            {
                "title": f"[Classcard status] {request_id}",
                "body": f"CLASSCARD_PROGRESS:0/{total}\nCLASSCARD_STATUS:queued",
            },
        )
        return str(issue["number"])
    except Exception as error:
        print(f"진행 상황 생성 경고: {error}", flush=True)
        return ""


def report_progress(progress, status="running", close=False):
    if not progress_issue:
        return
    body = (
        f"CLASSCARD_PAYLOAD:{encoded}\n\n"
        f"CLASSCARD_PROGRESS:{progress}\n"
        f"CLASSCARD_STATUS:{status}"
    )
    try:
        update = {"body": body}
        if close:
            update["state"] = "closed"
        github_request(f"/issues/{progress_issue}", update)
    except Exception as error:
        print(f"진행 상황 전송 경고: {error}", flush=True)


total = int(payload["end"]) - int(payload["start"]) + 1
progress_issue = ensure_progress_issue(total)
fallback_completed = 0
last_progress = ""
report_progress(f"0/{total}", "preparing")
process = subprocess.Popen(
    [sys.executable, "-u", "dynamic_learning.py", "--payload-base64", encoded],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace",
    bufsize=1,
)
for line in process.stdout:
    print(line, end="", flush=True)
    match = re.search(r"(?:진행|처리 완료|완료):\s*(\d+)\s*/\s*(\d+)", line)
    if match:
        progress = f"{match.group(1)}/{match.group(2)}"
    elif line.startswith("문제:"):
        fallback_completed = min(fallback_completed + 1, total)
        progress = f"{fallback_completed}/{total}"
    else:
        continue
    if progress != last_progress:
        report_progress(progress)
        last_progress = progress

return_code = process.wait()
report_progress(
    f"{total}/{total}" if return_code == 0 else (last_progress or f"0/{total}"),
    "completed" if return_code == 0 else "failed",
    close=True,
)
raise SystemExit(return_code)
