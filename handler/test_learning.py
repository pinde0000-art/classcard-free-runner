import html
import re
import threading
import time
from collections import Counter

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait


ENTRY_SELECTORS = [
    (By.XPATH, "/html/body/div[2]/div/div[2]/div[2]/div"),
    (
        By.XPATH,
        "//*[contains(normalize-space(.), '테스트')]"
        "[not(ancestor::*[contains(@class, 'modal')])]",
    ),
]

START_SELECTORS = [
    (By.CSS_SELECTOR, "#confirmModal .btn-ok"),
    (By.CSS_SELECTOR, "#alertModal .btn-ok"),
    (By.CSS_SELECTOR, "#wrapper-test .btn-condition-next"),
    (By.CSS_SELECTOR, "#wrapper-test .btn-quiz-start"),
    (By.CSS_SELECTOR, "#wrapper-test .btn-test-retry"),
    (By.XPATH, "//*[self::a or self::button][normalize-space(.)='응시']"),
    (By.XPATH, "//*[self::a or self::button][normalize-space(.)='새로 시작']"),
    (By.XPATH, "//*[self::a or self::button][normalize-space(.)='새로시작']"),
    (By.XPATH, "//*[self::a or self::button][normalize-space(.)='다음']"),
    (By.XPATH, "//*[self::a or self::button][normalize-space(.)='테스트 시작']"),
    (By.XPATH, "//*[self::a or self::button][normalize-space(.)='새로 시작']"),
]


class WatchdogTimeout(Exception):
    pass


def call_with_watchdog(func, timeout, *args, **kwargs):
    # Selenium 명령이 브라우저/CDP 쪽에서 멈추면 클라이언트 쪽 HTTP 타임아웃을
    # 걸어도 안 잡히는 경우가 있었다(31858703753 등 - 30초 타임아웃을 설정한
    # 뒤에도 동일한 지점에서 계속 멈춤). 전송 방식과 무관하게 동작하도록,
    # 별도 데몬 스레드에서 호출하고 join(timeout)으로 기다린다. 스레드가 실제로
    # 안 끝나도 데몬이라 메인 프로세스 종료를 막지 않는다.
    box = {}

    def target():
        try:
            box["value"] = func(*args, **kwargs)
        except BaseException as error:  # noqa: BLE001 - 호출자에게 그대로 재발생
            box["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise WatchdogTimeout(
            f"{getattr(func, '__name__', func)} 호출이 {timeout}초 안에 끝나지 않았습니다."
        )
    if "error" in box:
        raise box["error"]
    return box.get("value")


def norm_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def comparable_text(value):
    value = norm_text(value)
    value = re.sub(r"^\d+\s*[.)]\s*", "", value)
    return norm_text(value).casefold()


def window_is_open(driver):
    try:
        return bool(driver.window_handles)
    except Exception:
        return False


def click_visible(driver, selectors, timeout=8):
    end_time = time.time() + timeout
    while time.time() < end_time:
        if not window_is_open(driver):
            raise NoSuchWindowException("테스트 중 브라우저 창이 닫혔습니다.")
        for by, selector in selectors:
            for element in driver.find_elements(by, selector):
                try:
                    if not element.is_displayed() or not element.is_enabled():
                        continue
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        element,
                    )
                    driver.execute_script("arguments[0].click();", element)
                    return True
                except StaleElementReferenceException:
                    continue
        time.sleep(0.15)
    return False


def build_records(word_d):
    da_e, da_k, details = word_d
    records = []
    for front, back, detail in zip(da_e, da_k, details):
        front = norm_text(front)
        back = norm_text(back)
        if not front or not back:
            continue

        record = {
            "front": front,
            "back": back,
            "back_full": back,
            "examples": [],
        }
        if isinstance(detail, dict):
            record["back_full"] = norm_text(detail.get("back")) or back
            example = str(detail.get("example") or "")
            for line in example.splitlines():
                match = re.search(r"\{([^{}]+)\}", line)
                if not match:
                    continue
                answer = norm_text(match.group(1))
                prompt = norm_text(
                    line[: match.start()] + "_____" + line[match.end() :]
                )
                record["examples"].append((prompt, answer))
        records.append(record)
    return records


def clean_question(value):
    value = html.unescape(str(value or ""))
    value = re.split(r"<br\s*/?>|<div", value, maxsplit=1, flags=re.I)[0]
    return norm_text(re.sub(r"<[^>]+>", " ", value))


def meaning_senses(value):
    # '[명] 1. 복사(본) 2. 사본' -> {'복사(본)', '사본'}
    # 품사 표시([명]/[동] 등)와 뜻 번호를 걷어내고 개별 뜻만 남긴다.
    text = comparable_text(value)
    text = re.sub(r"\[[^\]]*\]", " ", text)
    parts = re.split(r"\d+\s*[.)]", text)
    senses = set()
    for part in parts:
        sense = norm_text(part).strip(" ,;/")
        if sense:
            senses.add(sense)
    return senses


def answer_candidates(question, records):
    question = clean_question(question)
    question_key = comparable_text(question)
    if not question_key:
        return []

    for record in records:
        for prompt, answer in record["examples"]:
            if comparable_text(prompt) == question_key:
                return [answer]

    for record in records:
        if comparable_text(record["front"]) == question_key:
            return [record["back"], record["back_full"]]
        if question_key in {
            comparable_text(record["back"]),
            comparable_text(record["back_full"]),
        }:
            return [record["front"]]

    # 테스트가 카드 뜻의 일부만 보여 주는 경우가 있다(예: 카드 뜻은
    # '[명] 1. 복사(본) 2. 사본'인데 문제는 '[명] 복사(본)'). 품사 표시와
    # 뜻 번호를 걷어내고 개별 뜻 단위로 비교해서 찾되, 후보가 하나로 좁혀질
    # 때만 사용한다(여러 카드가 걸리면 잘못 고를 수 있으므로 포기).
    question_senses = meaning_senses(question)
    if question_senses:
        partial = []
        for record in records:
            senses = meaning_senses(record["back"]) | meaning_senses(
                record["back_full"]
            )
            if senses & question_senses and record["front"] not in partial:
                partial.append(record["front"])
        if len(partial) == 1:
            return [partial[0]]

    return []


def visible_question_box(driver):
    for box in driver.find_elements(By.CSS_SELECTOR, "#testForm .box"):
        try:
            if not box.is_displayed():
                continue
            if box.find_elements(By.CSS_SELECTOR, "input[type='radio'], input[type='text']"):
                continue
            hidden = box.find_elements(By.CSS_SELECTOR, ".front-hidden")
            raw = (
                hidden[0].get_attribute("innerHTML")
                if hidden
                else box.get_attribute("innerText")
            )
            question = clean_question(raw)
            if question:
                return box, question
        except StaleElementReferenceException:
            continue
    return None, ""


def visible_answer_box(driver):
    for box in driver.find_elements(By.CSS_SELECTOR, "#testForm .box"):
        try:
            if box.is_displayed() and box.find_elements(
                By.CSS_SELECTOR, "input[type='radio'], input[type='text'], textarea"
            ):
                return box
        except StaleElementReferenceException:
            continue
    return None


def text_matches(candidate, answers):
    candidate_key = comparable_text(candidate)
    for answer in answers:
        answer_key = comparable_text(answer)
        if (
            candidate_key == answer_key
            or (answer_key and answer_key in candidate_key)
            or (candidate_key and candidate_key in answer_key)
        ):
            return True
    return False


def reveal_choices(driver):
    # 문제 전환 직후 첫 스페이스가 무시되는 경우가 있어 짧게 재시도한다.
    for _ in range(3):
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.SPACE)
        except StaleElementReferenceException:
            continue
        try:
            WebDriverWait(driver, 1.5, poll_frequency=0.03).until(
                lambda d: visible_answer_box(d) is not None
            )
            return
        except TimeoutException:
            continue

    state = norm_text(driver.execute_script("return document.body.innerText || '';"))
    raise RuntimeError(
        "테스트 선택지를 활성화하지 못했습니다. "
        f"현재 문제={current_number(driver)}, 화면={state[:250]!r}"
    )


def choose_answer(driver, answers):
    # 문항 전환 직후에는 선택지 영역이 잠깐 사라졌다가 다시 그려진다. 예전에는
    # 2초 안에 정답 라벨을 못 찾으면 곧바로 실패했는데, 그 사이 영역이 비어
    # 있으면 "선택지 영역을 찾지 못했습니다"로 끝나 버렸다(31862119060).
    # 영역이 나타날 때까지 먼저 넉넉히 기다린다.
    try:
        WebDriverWait(driver, 5, poll_frequency=0.05).until(
            lambda d: visible_answer_box(d) is not None
        )
    except TimeoutException:
        pass

    end_time = time.time() + 4
    while time.time() < end_time:
        answer_box = visible_answer_box(driver)
        if answer_box is None:
            time.sleep(0.05)
            continue

        inputs = answer_box.find_elements(By.CSS_SELECTOR, "input[type='radio']")
        for input_element in inputs:
            input_id = input_element.get_attribute("id")
            labels = answer_box.find_elements(
                By.CSS_SELECTOR, f"label[for='{input_id}']"
            )
            label = next(
                (element for element in labels if element.is_displayed()),
                None,
            )
            if label is not None and text_matches(label.text, answers):
                selected = driver.execute_script(
                    """
                    const input = arguments[0];
                    const label = arguments[1];
                    if (input.disabled) return false;
                    label.click();
                    if (!input.checked) input.click();
                    return input.checked;
                    """,
                    input_element,
                    label,
                )
                if selected:
                    return norm_text(label.text)
        time.sleep(0.03)

    answer_box = visible_answer_box(driver)
    if answer_box is None:
        raise RuntimeError("현재 테스트의 선택지 영역을 찾지 못했습니다.")

    typed = answer_box.find_elements(
        By.CSS_SELECTOR, "input[type='text'], textarea"
    )
    typed = next(
        (
            element
            for element in typed
            if element.is_displayed() and element.is_enabled()
        ),
        None,
    )
    if typed is not None:
        typed.click()
        typed.send_keys(Keys.CONTROL, "a")
        typed.send_keys(answers[0])
        typed.send_keys(Keys.ENTER)
        return answers[0]

    choices = [
        norm_text(label.text)
        for label in answer_box.find_elements(By.CSS_SELECTOR, "label")
        if label.is_displayed() and norm_text(label.text)
    ]
    raise RuntimeError(
        f"정답 {answers[0]!r}을 선택지에서 찾지 못했습니다. 선택지: {choices}"
    )


def normalize_sentence_token(value):
    value = str(value or "").casefold().replace("’", "'")
    return re.sub(r"[^\w']", "", value, flags=re.UNICODE)


def exact_sentence_token(value):
    value = str(value or "").replace("’", "'")
    return re.sub(r"[^\w']", "", value, flags=re.UNICODE)



# 여러 함수가 공유하는 조각: 현재 화면에 실제로 떠 있는 .test-sentence-words
# 컨테이너 하나("활성 컨테이너")를 찾는다. 문제가 바뀐 직후에도 이전 문항의
# 컨테이너가 DOM에 남아 있을 수 있으므로, 낱개 조각이 아니라 컨테이너 자체를
# 히트테스트해서 고른다.
_ACTIVE_SCRAMBLE_CONTAINER_JS = """
const passesBasic = (el) => {
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return r.width > 0 && r.height > 0
    && s.display !== 'none' && s.visibility !== 'hidden'
    && parseFloat(s.opacity || '1') > 0.05
    && r.bottom > 0 && r.top < innerHeight
    && r.right > 0 && r.left < innerWidth;
};
const containers = [...document.querySelectorAll(
  '#wrapper-test .test-sentence-words'
)];
let active = null;
for (const container of containers) {
  if (!passesBasic(container)) continue;
  const r = container.getBoundingClientRect();
  const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
  const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
  const hit = document.elementFromPoint(x, y);
  if (hit && (container === hit || container.contains(hit) || hit.contains(container))) {
    active = container;
    break;
  }
}
if (!active) {
  for (let index = containers.length - 1; index >= 0; index -= 1) {
    if (passesBasic(containers[index])) {
      active = containers[index];
      break;
    }
  }
}
"""


def scramble_choices(driver):
    return driver.execute_script(
        _ACTIVE_SCRAMBLE_CONTAINER_JS
        + """
        if (!active) return [];
        return [...active.querySelectorAll('a:not(.clicked)')].filter(el => {
          const r = el.getBoundingClientRect();
          const s = getComputedStyle(el);
          return r.width > 0 && r.height > 0 && s.display !== 'none'
            && s.visibility !== 'hidden' && parseFloat(s.opacity || '1') > 0.05
            && (el.innerText || '').trim();
        });
        """
    )


def scramble_choice_texts(driver):
    # 사이트가 조각 DOM을 매우 빠르게 다시 그려서, execute_script가 반환한 직후
    # Python에서 .text를 읽는 사이에 이미 stale이 되는 경우가 있다. 이런 조각은
    # 건너뛰고, WebDriverWait가 다음 폴링에서 새로 조회하도록 예외를 삼킨다.
    texts = []
    for element in scramble_choices(driver):
        try:
            texts.append(norm_text(element.text))
        except StaleElementReferenceException:
            continue
    return texts


def stable_scramble_choice_texts(driver, timeout=6):
    # 조각들이 하나씩 순차적으로 나타나는 등장 애니메이션 중에 조회하면 아직
    # 일부만 그려진 조각 목록을 잡을 수 있다(원문과 맞지 않는 조각 조합으로
    # 이어짐). 연속 두 번의 조회 결과가 같을 때까지 기다려 안정된 목록만
    # 반환한다.
    end_time = time.time() + timeout
    previous = None
    while time.time() < end_time:
        current = scramble_choice_texts(driver)
        if current:
            if previous is not None and sorted(current) == sorted(previous):
                return current
            previous = current
        time.sleep(0.12)
    if previous:
        return previous
    raise TimeoutException("문장 배열 조각 목록을 안정적으로 조회하지 못했습니다.")


def active_scramble_placed_count(driver):
    # 클릭이 실제로 반영됐는지는 우리 쪽 카운터가 아니라, 사이트가 그린
    # .test-sentence-input 안에 실제로 놓인 조각 수(자식 엘리먼트 수)로
    # 판단한다. innerText는 조각 사이에 공백이 없어 단어 수를 셀 수 없다.
    return driver.execute_script(
        _ACTIVE_SCRAMBLE_CONTAINER_JS
        + """
        if (!active) return null;
        let node = active;
        for (let i = 0; i < 8 && node; i += 1) {
          const input = node.querySelector('.test-sentence-input');
          if (input) {
            // 빈 자식(placeholder/캐럿 등)이나 글자 없는 조각('—')이 섞여
            // 있으면 원문 위치가 밀린다. '—'도 실제로 눌러서 답란에 들어가지만
            // 원문 단어는 아니므로, 글자나 숫자가 하나라도 있는 자식만 센다.
            return [...input.children].filter(el => {
              const text = (el.innerText || el.textContent || '').trim();
              return text && /[a-zA-Z0-9]/.test(text);
            }).length;
          }
          node = node.parentElement;
        }
        return null;
        """
    )


def dismiss_native_dialog(driver):
    # 네이티브 JS 다이얼로그(alert/confirm/beforeunload)가 열리면 렌더러가
    # 멈춰서 execute_script 계열 명령이 영영 반환하지 않는다. 실제로 한 줄의
    # 마지막 단어를 놓은 직후 이 상태로 빠져 워크플로 2시간 제한까지 조용히
    # 멈춘 사례가 있었다(31859164224 등). 알림창 조회/수락은 렌더러를 거치지
    # 않는 WebDriver 명령이라 이 상황에서도 동작한다.
    try:
        driver.switch_to.alert.accept()
        return True
    except Exception:
        return False


def guarded(driver, func, timeout, *args, **kwargs):
    # 렌더러를 건드리는 호출을 watchdog으로 감싼다. 멈추면 다이얼로그부터
    # 닫아 보고, 그래도 안 되면 호출자가 판단할 수 있도록 예외를 올린다.
    try:
        return call_with_watchdog(func, timeout, *args, **kwargs)
    except WatchdogTimeout:
        if dismiss_native_dialog(driver):
            return call_with_watchdog(func, timeout, *args, **kwargs)
        raise


def sentence_question(driver, records):
    text = norm_text(driver.execute_script("return document.body.innerText || '';"))
    matches = [
        record["back"]
        for record in records
        if record["back"] and comparable_text(record["back"]) in comparable_text(text)
    ]
    return max(matches, key=len) if matches else ""


def dismiss_test_focus_warning(driver):
    text = driver.execute_script("return document.body.innerText || '';")
    if "테스트에 집중하세요" not in text:
        return False
    return click_visible(
        driver,
        [(By.XPATH, "//*[self::a or self::button][normalize-space(.)='확인']")],
        timeout=2,
    )


def find_scramble_choice(driver, expected_token):
    # 한 줄 안에 대소문자만 다른 같은 단어가 함께 나올 수 있다(예: "The pilot
    # completed the flight safely"의 'The'와 'the'). 소문자로 접어서 비교하면
    # DOM 순서상 먼저 나오는 엉뚱한 조각을 누르게 되므로, 대소문자까지 맞는
    # 조각을 우선 고르고 없을 때만 접어서 비교한다.
    exact = exact_sentence_token(expected_token)
    normalized = normalize_sentence_token(expected_token)
    fallback = None
    for element in scramble_choices(driver):
        try:
            text = element.text
            if exact_sentence_token(text) == exact:
                return element
            if fallback is None and normalize_sentence_token(text) == normalized:
                fallback = element
        except StaleElementReferenceException:
            continue
    return fallback


def selected_sentence_token_count(driver):
    text = driver.execute_script(
        """
        return [...document.querySelectorAll('.test-sentence-input')]
          .map(el => el.innerText || '')
          .join(' ');
        """
    )
    return len(norm_text(text).split())


def native_pointer_click(driver, element):
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
        element,
    )
    time.sleep(0.03)
    point = driver.execute_script(
        """
        const r = arguments[0].getBoundingClientRect();
        return {x: r.left + r.width / 2, y: r.top + r.height / 2};
        """,
        element,
    )
    driver.execute_cdp_cmd(
        "Input.dispatchMouseEvent",
        {"type": "mouseMoved", "x": point["x"], "y": point["y"]},
    )
    for event_type in ("mousePressed", "mouseReleased"):
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {
                "type": event_type,
                "x": point["x"],
                "y": point["y"],
                "button": "left",
                "clickCount": 1,
            },
        )


def _find_click_confirm(driver, expected_token, expected_start):
    # solve_sentence_scramble에서 watchdog으로 감싸 호출하는 "조각 찾기 +
    # 클릭 + 반영 확인"의 본체. 이 함수 자체는 원래 코드와 동일하게 동작하되,
    # 그 어떤 하위 호출이 멈춰도 call_with_watchdog가 상한 시간 안에 제어권을
    # 돌려줄 수 있도록 별도 스레드에서 실행 가능한 형태로 분리했다.
    found = {}

    def locate(d, expected_token=expected_token, found=found):
        match = find_scramble_choice(d, expected_token)
        if match is None:
            return False
        found["choice"] = match
        return True

    WebDriverWait(driver, 3, poll_frequency=0.02).until(locate)

    # 이 위젯에서는 JS 클릭(element.click())이 아예 먹지 않는다(31860203008 -
    # 12번 시도해도 한 단어도 놓이지 않음). 반대로 원래 쓰던 CDP
    # Input.dispatchMouseEvent는 클릭 자체는 되지만, 한 줄의 마지막 단어를
    # 누른 직후 브라우저 세션 전체가 응답하지 않는 정지를 일으켰다
    # (31859761923). 원시 CDP 입력 주입은 WebDriver의 명령 처리와 동기화되지
    # 않아, 그 입력이 모달/전환을 유발하면 chromedriver가 교착될 수 있다.
    # 그래서 실제 입력 이벤트이면서 WebDriver를 통해 동기화되는 W3C Actions
    # API(ActionChains)를 쓴다.
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});", found["choice"]
    )
    ActionChains(driver).move_to_element(found["choice"]).click().perform()
    try:
        WebDriverWait(driver, 1.5, poll_frequency=0.02).until(
            lambda d: (active_scramble_placed_count(d) or 0) > expected_start
        )
        return True
    except TimeoutException:
        return False


def solve_sentence_scramble(driver, answer):
    raw_tokens = [
        token for token in str(answer or "").split()
        if normalize_sentence_token(token)
    ]
    if not raw_tokens:
        raise RuntimeError("문장 배열 정답이 비어 있습니다.")
    normalized = [normalize_sentence_token(token) for token in raw_tokens]

    # 줄 단위 상태 머신. line_start는 지금 줄이 시작될 때(문제 시작 시, 또는
    # 이전 줄 제출 직후) 이미 놓여 있던 단어 수다. 다음 줄에 속한 조각은
    # 화면/DOM에 미리 보여도(예: 'York'을 놓기 전부터 'and'가 목록에 있음)
    # 지금 줄을 제출하기 전에는 실제로 클릭이 먹지 않는다. 그래서 "조각이
    # 비었는지"가 아니라 "같은 단어를 여러 번 눌러도 진행되지 않는지"로 줄이
    # 끝났음을 판단한다.
    line_start = active_scramble_placed_count(driver) or 0
    just_revealed = True

    WORD_RETRY_LIMIT = 3   # 이만큼 실패하면 지금 줄이 끝났을 가능성을 의심한다
    STALL_LIMIT = 12       # 그래도 안 되면 무한 루프 대신 진단과 함께 실패
    word_attempts = 0
    tracked_position = -1
    # 같은 위치에서 제출을 두 번 이상 시도하지 않기 위한 표시(성급한 제출 방지).
    line_submit_tried_at = -1
    # 조각 목록이 아직 갱신되지 않은 과도기에는 원문과 안 맞을 수 있어
    # 몇 번은 기다렸다가 다시 읽는다.
    segment_retries = 0
    SEGMENT_RETRY_LIMIT = 10
    # 실제 단어 조각이 하나도 안 보이는 상태로 계속 도는 것을 막는 상한.
    # 다음 줄 조각이 나타나기를 먼저 충분히 기다린 뒤에만 제출을 시도한다.
    empty_pool_waits = 0
    EMPTY_POOL_SUBMIT_AFTER = 20   # 약 3초
    EMPTY_POOL_WAIT_LIMIT = 60     # 약 9초
    # 글자 없는 조각('—')도 눌러야 하는 경우를 위한 상한.
    decorative_clicks = 0
    DECORATIVE_CLICK_LIMIT = 4

    while True:
        # 한 줄의 마지막 단어를 놓은 직후 네이티브 다이얼로그가 떠서 렌더러가
        # 멈추는 경우가 있어, 매 반복 시작 시 먼저 확인해 닫는다. 세션이 통째로
        # 멈추면 이 조회조차 반환하지 않는 사례가 있어(31859447060) 이것도
        # watchdog으로 감싼다.
        try:
            call_with_watchdog(dismiss_native_dialog, 5, driver)
        except WatchdogTimeout:
            pass  # 상위 watchdog이 최종적으로 처리한다.
        try:
            guarded(driver, dismiss_test_focus_warning, 8, driver)
            placed = guarded(driver, active_scramble_placed_count, 8, driver)
        except WatchdogTimeout as error:
            raise RuntimeError(
                f"문장 배열 진행 상태를 읽는 중 브라우저 응답이 멈췄습니다"
                f"(watchdog {error})."
            ) from error
        expected_start = placed if placed is not None else tracked_position
        if expected_start < 0:
            expected_start = 0
        if expected_start >= len(raw_tokens):
            # 마지막 줄도 다른 줄과 마찬가지로 명시적인 제출이 필요하다.
            # 여기서 조각 배치가 끝났다고 바로 return하면 마지막 줄이 제출되지
            # 않은 채 남을 수 있다.
            submit = next(
                (
                    element
                    for element in driver.find_elements(
                        By.CSS_SELECTOR, ".btn-current-send-input"
                    )
                    if element.is_displayed() and element.is_enabled()
                ),
                None,
            )
            if submit is not None:
                driver.execute_script("arguments[0].click();", submit)
                time.sleep(0.2)
            return
        if expected_start != tracked_position:
            tracked_position = expected_start
            word_attempts = 0
        line_start = min(line_start, expected_start)

        try:
            choice_texts = guarded(driver, stable_scramble_choice_texts, 12, driver)
        except WatchdogTimeout as error:
            raise RuntimeError(
                f"문장 배열 조각 목록을 읽는 중 브라우저 응답이 멈췄습니다"
                f"(watchdog {error})."
            ) from error
        except TimeoutException:
            # 조각이 하나도 남지 않았다. 이 문항에서 눌러야 할 것을 다 눌러
            # 문항이 끝난 상태이므로(다음 문항 전환은 바깥 루프가 처리한다)
            # 여기서 정상 종료한다.
            if expected_start > 0:
                return
            raise
        choice_tokens = [
            normalize_sentence_token(text)
            for text in choice_texts
            if normalize_sentence_token(text)
        ]
        if not choice_tokens:
            # 글자 없는 조각('—')만 남은 상태. 31861168882의 DOM 덤프에서
            # 확인된 사실: 이 '—'는 장식이 아니라 아직 안 눌린 실제 선택지다
            # (나머지 단어 조각은 모두 'clicked correct'인데 '—'만 안 눌림).
            # 이 문제의 정답 표시는 카드 원문 전체가 아니라 앞부분까지이고,
            # 생략된 뒷부분을 '—' 조각이 대신한다. 그래서 남은 '—'도 눌러야
            # 문항이 완성된다.
            if choice_texts and decorative_clicks < DECORATIVE_CLICK_LIMIT:
                remaining = scramble_choices(driver)
                if remaining:
                    decorative_clicks += 1
                    try:
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block: 'center'});",
                            remaining[0],
                        )
                        ActionChains(driver).move_to_element(
                            remaining[0]
                        ).click().perform()
                    except (StaleElementReferenceException, TimeoutException):
                        pass
                    time.sleep(0.25)
                    continue

            empty_pool_waits += 1
            if (
                empty_pool_waits >= EMPTY_POOL_SUBMIT_AFTER
                and expected_start > line_start
                and line_submit_tried_at != expected_start
            ):
                submit = next(
                    (
                        element
                        for element in driver.find_elements(
                            By.CSS_SELECTOR, ".btn-current-send-input"
                        )
                        if element.is_displayed() and element.is_enabled()
                    ),
                    None,
                )
                if submit is not None:
                    line_submit_tried_at = expected_start
                    driver.execute_script("arguments[0].click();", submit)
                    line_start = expected_start
                    just_revealed = True
                    empty_pool_waits = 0
                    time.sleep(0.3)
                    continue
            if empty_pool_waits > EMPTY_POOL_WAIT_LIMIT:
                raise RuntimeError(
                    "문장 배열 조각이 나타나지 않습니다. "
                    f"남은 조각: {choice_texts}, 놓은 단어 수: {expected_start}"
                )
            time.sleep(0.15)
            continue
        empty_pool_waits = 0

        segment = None
        for start in range(expected_start, len(raw_tokens) - len(choice_tokens) + 1):
            end = start + len(choice_tokens)
            if Counter(normalized[start:end]) == Counter(choice_tokens):
                segment = raw_tokens[start:end]
                break
        if segment is None:
            # 방금 놓은 조각이 목록에서 사라지기 전에 읽으면(사이트가 제거
            # 애니메이션을 끝내기 전) placed count는 이미 늘었는데 조각 목록엔
            # 그 단어가 남아 있어, 원문의 잘못된 위치와 맞추려다 실패한다.
            # 과도기 상태이므로 잠깐 기다렸다가 다시 읽는다.
            segment_retries += 1
            if segment_retries <= SEGMENT_RETRY_LIMIT:
                time.sleep(0.2)
                continue
            raise RuntimeError(
                "문장 배열 조각을 원문과 맞추지 못했습니다. "
                f"현재 조각: {choice_texts}, 남은 원문: {raw_tokens[expected_start:]}"
            )
        segment_retries = 0

        if just_revealed:
            time.sleep(0.35)
            just_revealed = False

        expected_token = segment[0]

        try:
            registered = guarded(
                driver, _find_click_confirm, 10, driver, expected_token, expected_start
            )
        except WatchdogTimeout as error:
            # 클라이언트 쪽 HTTP 타임아웃을 걸어도 브라우저/CDP 쪽에서 멈추면
            # 안 잡히는 경우가 있었다(31858703753). 여기서는 전송 방식과
            # 무관하게 별도 스레드 join(10초)으로 강제 상한을 둔다.
            raise RuntimeError(
                f"문장 조각 {expected_token!r} 처리 중 브라우저 응답이 멈췄습니다"
                f"(watchdog {error})."
            ) from error
        except TimeoutException as error:
            raise RuntimeError(
                f"문장 조각 {expected_token!r}을 찾지 못했습니다. "
                f"현재 선택지: {choice_texts}"
            ) from error
        except StaleElementReferenceException:
            # 클릭 직전에 조각 DOM이 다시 그려졌다. word_attempts를 늘리지
            # 않고 다음 바깥 루프 반복에서 ground truth를 다시 읽는다.
            continue

        if registered:
            # 클릭이 실제로 반영됐다 - 다음 단어로 진행한다.
            word_attempts = 0
            time.sleep(0.08)
            continue

        # 이번 클릭은 반영되지 않았다.
        word_attempts += 1
        if word_attempts >= STALL_LIMIT:
            raise RuntimeError(
                f"문장 배열 {expected_start}번째 단어({expected_token!r})에서 "
                f"{STALL_LIMIT}번 클릭해도 진행되지 않았습니다."
            )

        # 단어 하나도 놓지 못한 상태(expected_start == line_start)에서는
        # 성급하게 제출하지 않고 그냥 재시도한다 - 이전에 제출 버튼이
        # "A" 한 단어만 놓인 상태에서도 활성화돼 있어, 여기서 제출을 시도하면
        # 그 뒤로 클릭이 전혀 먹지 않게 되는 문제가 있었다(31812103010).
        if (
            word_attempts >= WORD_RETRY_LIMIT
            and expected_start > line_start
            and line_submit_tried_at != expected_start
        ):
            line_submit_tried_at = expected_start
            previous_signature = tuple(sorted(choice_texts))
            submit = next(
                (
                    element
                    for element in driver.find_elements(
                        By.CSS_SELECTOR, ".btn-current-send-input"
                    )
                    if element.is_displayed() and element.is_enabled()
                ),
                None,
            )
            if submit is not None:
                driver.execute_script("arguments[0].click();", submit)
                try:
                    WebDriverWait(driver, 4, poll_frequency=0.05).until(
                        lambda d: tuple(sorted(scramble_choice_texts(d))) != previous_signature
                    )
                except TimeoutException:
                    pass  # 신호가 안 바뀌어도 다음 반복에서 ground truth로 재확인한다.
                line_start = active_scramble_placed_count(driver) or line_start
                just_revealed = True
                word_attempts = 0
                time.sleep(0.15)


def wait_for_next_question(driver, number):
    end_time = time.time() + 12
    while time.time() < end_time:
        if test_complete(driver) or (
            current_number(driver) is not None
            and current_number(driver) != number
        ):
            return

        candidates = driver.find_elements(
            By.XPATH,
            "//*[self::a or self::button]"
            "[normalize-space(.)='다음' or normalize-space(.)='다음 문제' "
            "or normalize-space(.)='계속' or normalize-space(.)='확인']",
        )
        candidates.extend(
            driver.find_elements(
                By.CSS_SELECTOR,
                ".test-bottom .btn-current-send-input, "
                ".btn-next, .btn-next-question, .btn-confirm",
            )
        )
        for element in candidates:
            try:
                if element.is_displayed() and element.is_enabled():
                    # 문항 전환 직후도 세션이 멈추기 쉬운 지점이라, 여기서도
                    # 원시 CDP 입력 대신 동기화되는 ActionChains를 쓴다.
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});", element
                    )
                    ActionChains(driver).move_to_element(element).click().perform()
                    time.sleep(0.12)
                    break
            except StaleElementReferenceException:
                continue
        time.sleep(0.1)

    raise TimeoutException(f"테스트 {number}번 다음 문제로 넘어가지 못했습니다.")


def current_number(driver):
    for element in driver.find_elements(By.CSS_SELECTOR, ".current-quest-num"):
        if element.is_displayed():
            match = re.search(r"\d+", element.text)
            if match:
                return int(match.group())
    return None


def test_complete(driver):
    text = driver.execute_script("return document.body.innerText || '';")
    return (
        "테스트 결과가 제출되었습니다" in text
        or "제출 결과 확인" in text
        or ("목표점수" in text and "완료" in text)
        or ("GOOD" in text and "JOB" in text)
    )


def test_ready(driver):
    return (
        current_number(driver) is not None
        and (
            visible_question_box(driver)[0] is not None
            or bool(scramble_choices(driver))
        )
    )


def start_test(driver):
    end_time = time.time() + 35
    while time.time() < end_time:
        if not window_is_open(driver):
            raise NoSuchWindowException("테스트 시작 중 브라우저 창이 닫혔습니다.")
        if test_ready(driver):
            return
        if click_visible(driver, START_SELECTORS, timeout=1):
            time.sleep(0.8)
        else:
            time.sleep(0.2)
    raise TimeoutException("테스트 문제를 시작하지 못했습니다.")


class TestLearning:
    def __init__(self, driver: webdriver.Chrome):
        self.driver = driver

    def run(self, num_d: int, word_d: list) -> int:
        driver = self.driver
        driver.execute_cdp_cmd(
            "Emulation.setFocusEmulationEnabled", {"enabled": True}
        )
        records = build_records(word_d)
        if not records:
            raise RuntimeError("테스트 정답에 사용할 카드 데이터가 없습니다.")

        if not click_visible(driver, ENTRY_SELECTORS, timeout=10):
            raise TimeoutException("테스트 진입 버튼을 찾지 못했습니다.")
        WebDriverWait(driver, 15).until(
            lambda d: "ClassTest" in d.current_url
            and bool(d.find_elements(By.ID, "wrapper-test"))
        )
        start_test(driver)

        completed = 0
        expected_count = max(1, num_d - 1)
        last_number = None
        same_number_since = None
        SAME_NUMBER_TIMEOUT = 20
        no_question_since = None
        NO_QUESTION_TIMEOUT = 20
        while not test_complete(driver):
            if not window_is_open(driver):
                raise NoSuchWindowException("테스트 중 브라우저 창이 닫혔습니다.")

            number = current_number(driver)
            _, question = visible_question_box(driver)
            is_sentence_scramble = bool(scramble_choices(driver))
            if not question and is_sentence_scramble:
                question = sentence_question(driver, records)
            if number is None or not question:
                # test_ready()가 매번 즉시 True를 반환하면 이 분기가 아무런
                # 대기 없이 계속 돌 수 있다(예: is_sentence_scramble 판정이
                # 흔들려 question이 계속 빈 채로 남는 경우). 일정 시간 이상
                # 문항 번호/질문을 못 읽으면 진단과 함께 실패시킨다.
                if no_question_since is None:
                    no_question_since = time.time()
                elif time.time() - no_question_since > NO_QUESTION_TIMEOUT:
                    state = norm_text(
                        driver.execute_script("return document.body.innerText || '';")
                    )
                    raise RuntimeError(
                        f"{NO_QUESTION_TIMEOUT}초 이상 문항 번호나 질문을 읽지 못했습니다. "
                        f"number={number}, is_sentence_scramble={is_sentence_scramble}, "
                        f"화면={state[:300]!r}"
                    )
                WebDriverWait(driver, 8).until(
                    lambda d: test_complete(d) or test_ready(d)
                )
                continue
            no_question_since = None
            if number == last_number:
                # wait_for_next_question()이 다음 문항으로의 전환을 잘못
                # 감지했거나(예: current_number()가 잠깐 None이었다가 다시
                # 이전 번호로 돌아옴) 사이트가 실제로 멈춘 경우, 0.1초 재시도만
                # 반복하면 아무 타임아웃 없이 계속 돈다. 일정 시간 이상
                # 그대로면 진단과 함께 실패시킨다.
                if same_number_since is None:
                    same_number_since = time.time()
                elif time.time() - same_number_since > SAME_NUMBER_TIMEOUT:
                    state = norm_text(
                        driver.execute_script("return document.body.innerText || '';")
                    )
                    raise RuntimeError(
                        f"테스트 문항 번호가 {number}에서 {SAME_NUMBER_TIMEOUT}초 이상 "
                        f"바뀌지 않았습니다. 화면={state[:300]!r}"
                    )
                time.sleep(0.1)
                continue
            same_number_since = None

            answers = answer_candidates(question, records)
            if not answers:
                raise RuntimeError(
                    f"테스트 {number}번 문제의 정답을 찾지 못했습니다: {question!r}"
                )

            if is_sentence_scramble:
                selected = answers[0]
                # 내부 어디서 멈추든 한 문항이 무한정 걸리지 않도록 최종 상한을
                # 둔다. 브라우저 세션이 통째로 멈추면 알림창 조회 같은 호출조차
                # 반환하지 않아, 안쪽에 개별 watchdog을 걸어도 빠져나오지 못한
                # 사례가 있었다(31859447060 - 워크플로 2시간 제한까지 정지).
                try:
                    call_with_watchdog(
                        solve_sentence_scramble, 150, driver, selected
                    )
                except WatchdogTimeout as error:
                    raise RuntimeError(
                        f"테스트 {number}번 문장 배열이 150초 안에 끝나지 않았습니다"
                        f"({error}). 브라우저 세션이 응답하지 않는 것으로 보입니다."
                    ) from error
            else:
                # 문항 전환 직후 선택지가 잠깐 사라지는 경우가 있어, 한 번
                # 실패하면 문항 번호가 그대로인지 확인하고 다시 시도한다.
                selected = None
                for attempt in range(3):
                    try:
                        reveal_choices(driver)
                        selected = choose_answer(driver, answers)
                        break
                    except RuntimeError:
                        if attempt == 2 or current_number(driver) != number:
                            raise
                        time.sleep(0.5)
            print(
                f"테스트 진행: {number}/{expected_count} - "
                f"{question!r} -> {selected!r}",
                flush=True,
            )
            completed += 1
            last_number = number

            wait_for_next_question(driver, number)
            if completed > expected_count + 2:
                raise RuntimeError("테스트 문항 수가 예상보다 많아 중단했습니다.")

        print(f"테스트 처리 완료: {completed}/{expected_count}", flush=True)
        return completed
