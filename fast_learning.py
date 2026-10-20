"""클래스카드의 학습 완료 요청을 이용한 초고속 학습 경로.

브라우저에서 카드를 한 장씩 누르는 대신, 클래스카드 학습 화면이 마지막에
전송하는 ``/ViewSetAsync/learnAll`` 요청을 같은 로그인 세션으로 보낸다.
전체 범위의 암기·리콜·스펠만 처리하며, 응답이나 반영 확인이 불확실하면
FastLearningUnavailable을 발생시켜 기존 Selenium 경로가 이어받게 한다.
"""

import html
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser


BASE_URL = "https://www.classcard.net"
MODE_INFO = {
    1: ("암기", "Memorize", 1),
    2: ("리콜", "Recall", 2),
    3: ("스펠", "Spell", 3),
}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class FastLearningUnavailable(RuntimeError):
    """빠른 경로를 안전하게 사용할 수 없어 브라우저 실행이 필요함."""


class SetPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.card_ids = []
        self._seen = set()
        self._bottom_depth = 0
        self._bottom_text = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = set(str(values.get("class") or "").split())
        if tag == "div" and "flip-card" in classes:
            card_id = str(values.get("data-idx") or "")
            if card_id.isdigit() and card_id not in self._seen:
                self._seen.add(card_id)
                self.card_ids.append(card_id)
        if self._bottom_depth and tag not in VOID_TAGS:
            self._bottom_depth += 1
        elif "bottom-fixed" in classes:
            self._bottom_depth = 1

    def handle_startendtag(self, tag, attrs):
        values = dict(attrs)
        classes = set(str(values.get("class") or "").split())
        if tag == "div" and "flip-card" in classes:
            card_id = str(values.get("data-idx") or "")
            if card_id.isdigit() and card_id not in self._seen:
                self._seen.add(card_id)
                self.card_ids.append(card_id)

    def handle_endtag(self, tag):
        if self._bottom_depth:
            self._bottom_depth -= 1

    def handle_data(self, data):
        if self._bottom_depth and str(data).strip():
            self._bottom_text.append(str(data).strip())

    @property
    def progress(self):
        text = " ".join(self._bottom_text)
        result = {}
        for name, _, _ in MODE_INFO.values():
            values = [
                int(value)
                for value in re.findall(
                    rf"{re.escape(name)}.{{0,60}}?(\d+)\s*%", text, re.IGNORECASE
                )
            ]
            if values:
                result[name] = max(values)
        return result


def _integer_variable(source, name, default=None):
    match = re.search(
        rf"(?:var\s+)?{re.escape(name)}\s*=\s*['\"]?(\d+)", source
    )
    if match:
        return int(match.group(1))
    return default


class ClasscardHttpClient:
    def __init__(self):
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            )
        }

    def _open(self, path, data=None, referer=None):
        body = None
        headers = dict(self.headers)
        if data is not None:
            body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            headers["X-Requested-With"] = "XMLHttpRequest"
        if referer:
            headers["Referer"] = BASE_URL + referer
        request = urllib.request.Request(BASE_URL + path, data=body, headers=headers)
        with self.opener.open(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")

    def login(self, login_id, login_password):
        self._open("/Login")
        raw = self._open(
            "/LoginProc",
            {"login_id": login_id, "login_pwd": login_password},
            "/Login",
        )
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as error:
            raise FastLearningUnavailable("빠른 로그인 응답을 해석하지 못했습니다.") from error
        if result.get("result") != "ok":
            raise FastLearningUnavailable("빠른 로그인에 실패했습니다.")

    def get(self, path):
        return self._open(path)

    def post_json(self, path, data, referer):
        raw = self._open(path, data, referer)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise FastLearningUnavailable("빠른 학습 응답을 해석하지 못했습니다.") from error


def run_fast(payload, modes, login_id, login_password, on_mode_done=None):
    """전체 카드 학습을 서버 요청으로 즉시 완료한다.

    성공하면 모드 목록을 반환한다. 조금이라도 확실하지 않으면 예외를 발생시켜
    호출자가 기존 브라우저 자동화를 실행하도록 한다.
    """

    start = int(payload["start"])
    end = int(payload["end"])
    card_count = int(payload["card_count"])
    amount = int(payload["amount"])
    if start != 1 or end != card_count:
        raise FastLearningUnavailable("부분 카드 범위는 안전한 브라우저 경로로 처리합니다.")
    if not modes or any(mode not in MODE_INFO for mode in modes):
        raise FastLearningUnavailable("테스트 모드는 기존 브라우저 경로로 처리합니다.")
    if not login_id or not login_password:
        raise FastLearningUnavailable("빠른 학습에 필요한 로그인 정보가 없습니다.")

    set_id = str(payload["set_id"])
    class_id = str(payload["class_id"])
    client = ClasscardHttpClient()
    client.login(login_id, login_password)

    set_path = f"/set/{set_id}/{class_id}"
    set_source = client.get(set_path)
    parser = SetPageParser()
    parser.feed(set_source)
    card_ids = parser.card_ids
    if len(card_ids) != card_count:
        raise FastLearningUnavailable(
            f"빠른 학습 카드 수가 다릅니다({len(card_ids)}/{card_count})."
        )

    progress = parser.progress
    for mode in modes:
        mode_name, route, activity = MODE_INFO[mode]
        target_progress = amount * 100
        current_progress = int(progress.get(mode_name, 0) or 0)
        if current_progress >= target_progress:
            if on_mode_done:
                on_mode_done(mode)
            continue

        learn_path = f"/{route}/{set_id}/6000/{class_id}"
        learn_source = client.get(learn_path)
        user_id = _integer_variable(learn_source, "c_u", 0)
        if not user_id:
            raise FastLearningUnavailable("로그인된 학습 사용자 번호를 확인하지 못했습니다.")
        current_round = _integer_variable(
            learn_source,
            "current_round",
            max(1, current_progress // 100 + 1),
        )
        first_round = max(1, current_progress // 100 + 1, current_round)
        if first_round > amount:
            if on_mode_done:
                on_mode_done(mode)
            continue

        for round_number in range(first_round, amount + 1):
            result = client.post_json(
                "/ViewSetAsync/learnAll",
                {
                    "set_idx_2": set_id,
                    "card_idx[]": card_ids,
                    "score[]": [1] * len(card_ids),
                    "activity": activity,
                    "last_section": 1,
                    "last_round": round_number,
                    "view_cnt": len(card_ids),
                    "user_idx": user_id,
                    "class_idx": class_id,
                    "is_know_card": 1,
                    "is_w_pro": _integer_variable(learn_source, "is_w_pro", 0),
                },
                learn_path,
            )
            if result.get("result") != "ok":
                raise FastLearningUnavailable(
                    f"{mode_name} {round_number * 100}% 빠른 완료 요청이 거부됐습니다."
                )

        # 진행률 마크업을 읽을 수 있는 경우에는 실제 반영까지 확인한다.
        verified_source = client.get(set_path)
        verified_parser = SetPageParser()
        verified_parser.feed(verified_source)
        verified = verified_parser.progress.get(mode_name)
        if verified is not None and verified < target_progress:
            raise FastLearningUnavailable(
                f"{mode_name} 빠른 완료가 아직 반영되지 않았습니다({verified}%)."
            )
        if on_mode_done:
            on_mode_done(mode)

    return list(modes)
