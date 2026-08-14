import base64
import json
import os
import re
import subprocess
import sys


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
raise SystemExit(
    subprocess.call(
        [sys.executable, "-u", "dynamic_learning.py", "--payload-base64", encoded]
    )
)
