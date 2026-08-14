import html
import re
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
    end_time = time.time() + 2
    while time.time() < end_time:
        answer_box = visible_answer_box(driver)
        if answer_box is None:
            time.sleep(0.03)
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


def visible_scramble_choices(driver):
    return driver.execute_script(
        """
        return [...document.querySelectorAll(
            '#wrapper-test .test-sentence-words a:not(.clicked)'
        )].filter(el => {
          const r = el.getBoundingClientRect();
          const s = getComputedStyle(el);
          const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
          const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
          const hit = document.elementFromPoint(x, y);
          return r.width > 0 && r.height > 0 && s.display !== 'none'
            && s.visibility !== 'hidden'
            && r.bottom > 0 && r.top < innerHeight
            && r.right > 0 && r.left < innerWidth && hit
            && (hit === el || el.contains(hit) || hit.contains(el))
            && (el.innerText || '').trim();
        });
        """
    )


def visible_scramble_choice_texts(driver):
    return driver.execute_script(
        """
        return [...document.querySelectorAll(
          '#wrapper-test .test-sentence-words a:not(.clicked)'
        )].filter(el => {
          const r = el.getBoundingClientRect();
          const s = getComputedStyle(el);
          const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
          const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
          const hit = document.elementFromPoint(x, y);
          return r.width > 0 && r.height > 0 && s.display !== 'none'
            && s.visibility !== 'hidden'
            && r.bottom > 0 && r.top < innerHeight
            && r.right > 0 && r.left < innerWidth && hit
            && (hit === el || el.contains(hit) || hit.contains(el))
            && (el.innerText || '').trim();
        }).map(el => (el.innerText || '').trim());
        """
    )


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


def find_scramble_choice(driver, expected):
    return driver.execute_script(
        r"""
        const expected = arguments[0];
        return [...document.querySelectorAll(
          '#wrapper-test .test-sentence-words a:not(.clicked)'
        )].find(el => {
          const r = el.getBoundingClientRect();
          const s = getComputedStyle(el);
          const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
          const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
          const hit = document.elementFromPoint(x, y);
          const token = (el.innerText || '').toLocaleLowerCase()
            .replace(/\u2019/g, "'")
            .replace(/[^\p{L}\p{N}_']/gu, '');
          return r.width > 0 && r.height > 0 && s.display !== 'none'
            && s.visibility !== 'hidden'
            && r.bottom > 0 && r.top < innerHeight
            && r.right > 0 && r.left < innerWidth && hit
            && (hit === el || el.contains(hit) || hit.contains(el))
            && token === expected;
        }) || null;
        """,
        expected,
    )


def visible_scramble_choice_count(driver):
    return driver.execute_script(
        """
        return [...document.querySelectorAll(
          '#wrapper-test .test-sentence-words a:not(.clicked)'
        )].filter(el => {
          const r = el.getBoundingClientRect();
          const s = getComputedStyle(el);
          const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
          const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
          const hit = document.elementFromPoint(x, y);
          return r.width > 0 && r.height > 0 && s.display !== 'none'
            && s.visibility !== 'hidden' && r.bottom > 0 && r.top < innerHeight
            && r.right > 0 && r.left < innerWidth && hit
            && (hit === el || el.contains(hit) || hit.contains(el))
            && (el.innerText || '').trim();
        }).length;
        """
    )


def selected_sentence_token_count(driver):
    text = driver.execute_script(
        """
        const el = [...document.querySelectorAll('.test-sentence-input')]
          .find(node => {
            const r = node.getBoundingClientRect();
            const style = getComputedStyle(node);
            return r.width > 0 && r.height > 0 && style.display !== 'none'
              && style.visibility !== 'hidden';
          });
        return el ? (el.innerText || '') : '';
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


def solve_sentence_scramble(driver, answer):
    raw_tokens = [
        token for token in str(answer or "").split()
        if normalize_sentence_token(token)
    ]
    if not raw_tokens:
        raise RuntimeError("문장 배열 정답이 비어 있습니다.")
    normalized = [normalize_sentence_token(token) for token in raw_tokens]
    expected_start = 0

    while expected_start < len(raw_tokens):
        dismiss_test_focus_warning(driver)
        choice_texts = WebDriverWait(driver, 6, poll_frequency=0.02).until(
            lambda d: visible_scramble_choice_texts(d) or None
        )
        choice_tokens = [normalize_sentence_token(text) for text in choice_texts]

        segment = None
        segment_end = expected_start
        for start in range(expected_start, len(raw_tokens) - len(choice_tokens) + 1):
            end = start + len(choice_tokens)
            if Counter(normalized[start:end]) == Counter(choice_tokens):
                segment = raw_tokens[start:end]
                segment_end = end
                break
        if segment is None:
            raise RuntimeError(
                "문장 배열 조각을 원문과 맞추지 못했습니다. "
                f"현재 조각: {choice_texts}, 남은 원문: {raw_tokens[expected_start:]}"
            )

        # 등장 애니메이션 중에는 클릭이 무시되므로 짧게 안정화한 뒤 누른다.
        time.sleep(0.35)
        for expected_token in segment:
            expected = normalize_sentence_token(expected_token)
            selected = False
            for _ in range(5):
                before_choices = len(visible_scramble_choice_texts(driver))
                try:
                    choice = WebDriverWait(driver, 1, poll_frequency=0.02).until(
                        lambda d: find_scramble_choice(d, expected)
                    )
                    native_pointer_click(driver, choice)
                    WebDriverWait(driver, 0.7, poll_frequency=0.02).until(
                        lambda d: len(visible_scramble_choice_texts(d))
                        < before_choices
                    )
                    selected = True
                    break
                except StaleElementReferenceException:
                    if len(visible_scramble_choice_texts(driver)) < before_choices:
                        selected = True
                        break
                    time.sleep(0.08)
                except TimeoutException:
                    time.sleep(0.12)
            if not selected:
                raise RuntimeError(f"문장 조각 {expected_token!r}을 선택하지 못했습니다.")
            time.sleep(0.03)

        previous_signature = tuple(choice_texts)
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
        if submit is None:
            raise RuntimeError("문장 배열 제출 버튼을 찾지 못했습니다.")
        driver.execute_script("arguments[0].click();", submit)
        expected_start = segment_end

        if expected_start < len(raw_tokens):
            WebDriverWait(driver, 6, poll_frequency=0.03).until(
                lambda d: tuple(
                    element.text.strip()
                    for element in visible_scramble_choices(d)
                ) != previous_signature
            )


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
                    native_pointer_click(driver, element)
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
            or bool(visible_scramble_choices(driver))
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
        while not test_complete(driver):
            if not window_is_open(driver):
                raise NoSuchWindowException("테스트 중 브라우저 창이 닫혔습니다.")

            number = current_number(driver)
            _, question = visible_question_box(driver)
            is_sentence_scramble = bool(visible_scramble_choices(driver))
            if not question and is_sentence_scramble:
                question = sentence_question(driver, records)
            if number is None or not question:
                WebDriverWait(driver, 8).until(
                    lambda d: test_complete(d) or test_ready(d)
                )
                continue
            if number == last_number:
                time.sleep(0.1)
                continue

            answers = answer_candidates(question, records)
            if not answers:
                raise RuntimeError(
                    f"테스트 {number}번 문제의 정답을 찾지 못했습니다: {question!r}"
                )

            if is_sentence_scramble:
                selected = answers[0]
                solve_sentence_scramble(driver, selected)
            else:
                reveal_choices(driver)
                selected = choose_answer(driver, answers)
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
