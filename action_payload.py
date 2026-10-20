import base64
import json
import os
import queue
import re
import subprocess
import sys
import threading
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


MODE_NAMES = {1: "암기", 2: "리콜", 3: "스펠", 4: "테스트"}
MODE_MARKER = re.compile(r"^\s*\[CLJ-MODES:([^\]]*)\]\s*", re.IGNORECASE)


def prepare_modes(value):
    """제목의 다중 모드 마커를 떼고 실행할 모드 목록을 만든다."""
    title = str(value.get("title") or "")
    marker = MODE_MARKER.match(title)
    raw_modes = value.get("modes")
    if marker:
        raw_modes = marker.group(1).split(",")
        title = title[marker.end():].strip()
    elif title.lstrip().upper().startswith("[CLJ-MODES:"):
        raise RuntimeError("학습 모드 마커 형식이 올바르지 않습니다.")

    if raw_modes is None:
        raw_modes = [value.get("mode")]
    elif isinstance(raw_modes, str):
        raw_modes = raw_modes.split(",")

    modes = []
    try:
        for item in raw_modes:
            mode = int(str(item).strip())
            if mode not in MODE_NAMES:
                raise ValueError
            if mode not in modes:
                modes.append(mode)
    except (TypeError, ValueError):
        raise RuntimeError(
            "학습 모드 마커에는 1, 2, 3 또는 단독 테스트 4만 사용할 수 있습니다."
        ) from None
    if not modes:
        raise RuntimeError("실행할 학습 모드가 없습니다.")
    if len(modes) > 1 and 4 in modes:
        raise RuntimeError("테스트는 다른 학습 모드와 동시에 실행할 수 없습니다.")

    value["title"] = title
    value["mode"] = modes[0]
    value["modes"] = modes
    return modes


modes = prepare_modes(payload)

# 계정 모드에서는 GitHub Secrets 대신 Worker에서 일회용으로 자격 증명을 받는다.
# 계정 식별자가 없으면 기존 단일 계정 방식을 그대로 쓴다.
_account_id = os.environ.get("INPUT_ACCOUNT_ID", "").strip()
if _account_id:
    from runner_account import load_credentials
    load_credentials(os.environ.get("INPUT_REQUEST_ID", ""))
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


cards_per_mode = int(payload["end"]) - int(payload["start"]) + 1
total = cards_per_mode * len(modes)
progress_issue = ensure_progress_issue(total)
mode_progress = {
    mode: {"done": 0, "fallback": 0}
    for mode in modes
}
last_progress = ""
progress_pattern = re.compile(
    r"(?:진행|처리 완료|완료):\s*(\d+)\s*/\s*(\d+)"
)


def aggregate_progress():
    return f"{sum(item['done'] for item in mode_progress.values())}/{total}"


def publish_progress():
    global last_progress
    progress = aggregate_progress()
    if progress != last_progress:
        report_progress(progress)
        last_progress = progress


def finish_fast_mode(mode):
    """빠른 완료가 확인된 모드를 전체 완료로 집계한다."""
    mode_progress[mode]["done"] = cards_per_mode
    publish_progress()


def try_fast_learning():
    """의존성 없는 HTTP 경로를 먼저 시도하고, 불확실하면 False를 반환한다."""
    if os.environ.get("CLASSCARD_FAST", "1") == "0":
        return False
    try:
        from fast_learning import FastLearningUnavailable, run_fast
    except Exception as error:
        print(f"초고속 경로 준비 오류: {error}", flush=True)
        return False
    try:
        print("초고속 학습을 시작합니다.", flush=True)
        run_fast(
            payload,
            modes,
            os.environ.get("CLASSCARD_ID", ""),
            os.environ.get("CLASSCARD_PASSWORD", ""),
            on_mode_done=finish_fast_mode,
        )
        print("초고속 학습이 완료되었습니다.", flush=True)
        return True
    except FastLearningUnavailable as error:
        print(f"초고속 경로 전환: {error}", flush=True)
    except Exception as error:
        print(f"초고속 경로 오류: {error}", flush=True)
    return False


def ensure_browser_dependencies():
    """빠른 경로 실패 시에만 기존 Selenium 의존성을 설치한다."""
    if os.environ.get("CLASSCARD_DEPENDENCIES_READY") == "1":
        return
    try:
        import bs4  # noqa: F401
        import requests  # noqa: F401
        import selenium  # noqa: F401
    except ImportError:
        print("기존 브라우저 학습에 필요한 패키지를 설치합니다.", flush=True)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
        )


def handle_output(mode, line):
    """원본 로그를 모드별로 표시하고 그 모드의 카드 진행을 합산한다."""
    print(f"[{MODE_NAMES[mode]}] {line}", end="", flush=True)
    state = mode_progress[mode]
    match = progress_pattern.search(line)
    if match:
        completed = min(int(match.group(1)), cards_per_mode)
        state["done"] = max(state["done"], completed)
    elif line.startswith("문제:"):
        state["fallback"] = min(state["fallback"] + 1, cards_per_mode)
        state["done"] = max(state["done"], state["fallback"])
    else:
        return
    publish_progress()


def start_mode(mode):
    mode_payload = dict(payload)
    mode_payload["mode"] = mode
    mode_payload.pop("modes", None)
    mode_encoded = base64.urlsafe_b64encode(
        json.dumps(mode_payload, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    return subprocess.Popen(
        [
            sys.executable,
            "-u",
            "dynamic_learning.py",
            "--payload-base64",
            mode_encoded,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


def finish_mode(mode, process):
    return_code = process.wait()
    if return_code == 0:
        mode_progress[mode]["done"] = cards_per_mode
        publish_progress()
    return return_code


def run_sequentially():
    return_codes = {}
    for mode in modes:
        try:
            process = start_mode(mode)
        except Exception as error:
            print(f"[{MODE_NAMES[mode]}] 실행 시작 실패: {error}", flush=True)
            return_codes[mode] = 1
            continue
        for line in process.stdout:
            handle_output(mode, line)
        return_codes[mode] = finish_mode(mode, process)
    return return_codes


def read_process_output(mode, process, output_queue):
    try:
        for line in process.stdout:
            output_queue.put((mode, line))
    finally:
        output_queue.put((mode, None))


def run_in_parallel():
    processes = {}
    return_codes = {}
    output_queue = queue.Queue()
    for mode in modes:
        try:
            process = start_mode(mode)
        except Exception as error:
            print(f"[{MODE_NAMES[mode]}] 실행 시작 실패: {error}", flush=True)
            return_codes[mode] = 1
            continue
        processes[mode] = process
        threading.Thread(
            target=read_process_output,
            args=(mode, process, output_queue),
            daemon=True,
        ).start()

    active = set(processes)
    while active:
        mode, line = output_queue.get()
        if line is None:
            active.discard(mode)
        else:
            handle_output(mode, line)
    for mode, process in processes.items():
        return_codes[mode] = finish_mode(mode, process)
    return return_codes


full_range = (
    int(payload["start"]) == 1
    and int(payload["end"]) == int(payload["card_count"])
)
publish_progress()
if try_fast_learning():
    report_progress(f"{total}/{total}", "completed", close=True)
    raise SystemExit(0)

ensure_browser_dependencies()
if len(modes) > 1 and full_range:
    print("전체 카드 범위이므로 선택한 학습 모드를 병렬 실행합니다.", flush=True)
    return_codes = run_in_parallel()
else:
    if len(modes) > 1:
        print(
            "부분 카드 범위의 중요 카드 충돌을 막기 위해 학습 모드를 순차 실행합니다.",
            flush=True,
        )
    return_codes = run_sequentially()

all_succeeded = (
    len(return_codes) == len(modes)
    and all(return_codes.get(mode) == 0 for mode in modes)
)
final_progress = f"{total}/{total}" if all_succeeded else aggregate_progress()
report_progress(
    final_progress,
    "completed" if all_succeeded else "failed",
    close=True,
)
if all_succeeded:
    raise SystemExit(0)
raise SystemExit(next((code for code in return_codes.values() if code), 1))
