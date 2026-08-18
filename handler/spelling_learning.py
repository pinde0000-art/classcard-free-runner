import re
import time
from collections import Counter
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


START_BUTTON_SELECTORS = [
    (
        By.XPATH,
        "//*[self::a or self::button]"
        "[contains(normalize-space(.), '스펠학습')"
        " and contains(normalize-space(.), '전체구간')]",
    ),
    (By.CSS_SELECTOR, ".btn-opt-start"),
    (By.CSS_SELECTOR, "#wrapper-learn .btn-opt-start"),
    (By.CSS_SELECTOR, "#wrapper-learn a.btn-success"),
    (By.CSS_SELECTOR, "#wrapper-learn a[class*='start']"),
    (By.CSS_SELECTOR, "#wrapper-learn .start-opt-body a"),
    (By.XPATH, "//*[@id='wrapper-learn']//a[contains(normalize-space(.), '시작')]"),
    (By.XPATH, "//*[@id='wrapper-learn']//button[contains(normalize-space(.), '시작')]"),
]

ENTRY_SELECTORS = [
    (By.CSS_SELECTOR, "[href*='/Spell/']"),
    (By.CSS_SELECTOR, "[onclick*='/Spell/']"),
    (By.CSS_SELECTOR, "[data-href*='/Spell/']"),
    (
        By.XPATH,
        "//*[normalize-space(.)='스펠' or normalize-space(.)='스펠학습']"
        "/ancestor-or-self::*[self::a or @onclick][1]",
    ),
]

IGNORE_TEXTS = {
    "학습중...",
    "알아요",
    "몰라요",
    "정답",
    "대소문자를 틀리게 입력했습니다.",
}


def norm_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def click_first_available(driver, selectors, timeout=12):
    wait = WebDriverWait(driver, timeout)
    last_error = None
    for by, selector in selectors:
        try:
            element = wait.until(EC.element_to_be_clickable((by, selector)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            driver.execute_script("arguments[0].click();", element)
            return element
        except TimeoutException as error:
            last_error = error
    raise last_error


def enter_spell(driver, timeout=15):
    end_time = time.time() + timeout
    while time.time() < end_time:
        if "/Spell/" in driver.current_url:
            return True
        for by, selector in ENTRY_SELECTORS:
            for element in driver.find_elements(by, selector):
                try:
                    if not element.is_displayed() or not element.is_enabled():
                        continue
                    target = " ".join(
                        [
                            element.get_attribute("href") or "",
                            element.get_attribute("onclick") or "",
                            element.get_attribute("data-href") or "",
                        ]
                    )
                    if "/Spell/" not in target and "스펠" not in element.text:
                        continue
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        element,
                    )
                    try:
                        element.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", element)
                    WebDriverWait(driver, 5).until(
                        lambda d: "/Spell/" in d.current_url
                    )
                    return True
                except Exception:
                    continue
        time.sleep(0.25)
    return False


def visible_texts(driver, selector):
    return driver.execute_script(
        """
        return Array.from(document.querySelectorAll(arguments[0]))
            .filter(el => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && rect.width > 0
                    && rect.height > 0;
            })
            .map(el => el.innerText || el.textContent || '')
            .filter(Boolean);
        """,
        selector,
    )


# 답을 맞힌 카드는 .current 를 유지한 채 .deactive 가 붙는다. 이걸 거르지
# 않으면 방금 푼 카드의 입력칸과 문제를 계속 다시 읽어서 제자리에 멈춘다.
CARD_SELECTORS = [
    ".CardItem.current.showing:not(.deactive)",
    ".CardItem.current:not(.deactive)",
    ".CardItem.active:not(.deactive)",
    ".CardItem.showing:not(.deactive):not(.previous):not(.next)",
]


def current_card_element(driver):
    for selector in CARD_SELECTORS:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if element.is_displayed() and element.rect["width"] > 0:
                    return element
            except Exception:
                continue
    return None


def current_card_id(driver):
    # 뜻이 같은 카드가 두 장이면 문제 텍스트만으로는 카드가 넘어갔는지 알 수
    # 없다. 리콜 학습처럼 data-idx 로 구분한다. 속성이 없으면 빈 문자열을
    # 돌려주고, 그때는 호출부가 텍스트 비교로 되돌아간다.
    card = current_card_element(driver)
    if card is None:
        return ""
    try:
        return card.get_attribute("data-idx") or ""
    except StaleElementReferenceException:
        return ""


def usable_input(element):
    try:
        if not element.is_displayed() or not element.is_enabled():
            return False
        return not element.get_attribute("readonly")
    except StaleElementReferenceException:
        return False


def get_spell_input(driver, timeout=2.0):
    """입력칸을 돌려준다. 못 찾으면 잠깐 기다렸다가 다시 본다.

    카드가 넘어가길 기다릴 때 쓰는 read_card_state 와 같은 자바스크립트가
    고른 요소를 그대로 받는다. 예전에는 여기서 파이썬이 따로 찾느라, 대기
    쪽은 "입력칸 있음"인데 여기서는 못 찾는 어긋남이 났다. 카드 전환 중에
    잠시 사라지는 순간에 걸리면 그대로 실패했으므로 짧게 재시도한다.
    """
    end_time = time.time() + timeout
    while True:
        element = read_card_state(driver).get("input")
        if element is not None:
            return element
        element = find_spell_input_fallback(driver)
        if element is not None:
            return element
        if time.time() >= end_time:
            break
        time.sleep(0.05)
    state = driver.execute_script(
        "return (document.body.innerText || '').slice(-600);"
    )
    raise TimeoutException(f"입력칸을 찾지 못했습니다. 현재 화면: {state}")


def find_spell_input_fallback(driver):
    input_class = driver.execute_script("return window.cheat_input_class || ''; ")
    selectors = []
    if input_class:
        selectors.append(f"input.{input_class}")
    selectors.extend([
        "input[name='input_answer']",
        "input[type='text']",
        "input",
    ])

    card = current_card_element(driver)
    if card is not None:
        for selector in selectors:
            try:
                for element in card.find_elements(By.CSS_SELECTOR, selector):
                    if usable_input(element):
                        return element
            except StaleElementReferenceException:
                break

    # 활성 카드를 못 찾았을 때만 학습 영역 전체를 훑는다. 이때도 이미 답한
    # 카드는 건너뛴다.
    for selector in selectors:
        for element in driver.find_elements(
            By.CSS_SELECTOR, f"#wrapper-learn .CardItem:not(.deactive) {selector}"
        ):
            if usable_input(element):
                return element
    return None


def has_spell_input(driver):
    # 화면 상태를 볼 뿐이므로 재시도하지 않는다. start_spell 처럼 "아직
    # 준비가 안 됐다"를 판단하는 쪽에서 기다림이 겹치면 느려진다.
    if read_card_state(driver).get("hasInput"):
        return True
    return find_spell_input_fallback(driver) is not None


def visible_element(driver, selector):
    for element in driver.find_elements(By.CSS_SELECTOR, selector):
        try:
            if element.is_displayed() and element.is_enabled():
                return element
        except Exception:
            continue
    return None


def sentence_spell_ready(driver):
    prompt = visible_element(driver, ".CardItem.active .para_item.active")
    choice = visible_element(driver, ".CardItem.active .scramble-item")
    return prompt is not None and choice is not None


def normalize_sentence_token(value):
    value = re.sub(r"^[^\w']+|[^\w']+$", "", str(value or ""), flags=re.UNICODE)
    return value.casefold()


def sentence_spell_progress(driver):
    known = visible_element(driver, ".known_count")
    total = visible_element(driver, ".total_count")
    if known is None or total is None:
        return None
    try:
        return int(known.text.strip()), int(total.text.strip())
    except (TypeError, ValueError):
        return None


def sentence_spell_signature(driver):
    prompt = visible_element(driver, ".CardItem.active .para_item.active")
    choices = [
        element.text.strip()
        for element in driver.find_elements(
            By.CSS_SELECTOR, ".CardItem.active .scramble-item"
        )
        if element.is_displayed()
    ]
    return prompt.text.strip() if prompt is not None else "", tuple(choices)


def sentence_spell_answer(driver, prompt, da_e, da_k, expected_start=0):
    prompt = norm_text(prompt)
    choices = [
        element.text.strip()
        for element in driver.find_elements(
            By.CSS_SELECTOR, ".CardItem.active .scramble-item"
        )
        if element.is_displayed()
    ]
    choice_tokens = [
        normalize_sentence_token(token)
        for token in choices
        if normalize_sentence_token(token)
    ]
    candidates = []

    for english, korean in zip(da_e, da_k):
        english = norm_text(english)
        korean = norm_text(korean)
        if not english:
            continue
        raw_tokens = [
            token for token in english.split() if normalize_sentence_token(token)
        ]
        normalized = [
            normalize_sentence_token(token) for token in raw_tokens
        ]
        for start in range(len(normalized) - len(choice_tokens) + 1):
            end = start + len(choice_tokens)
            if Counter(normalized[start:end]) != Counter(choice_tokens):
                continue
            prompt_score = int(prompt == korean) * 2 + int(prompt and prompt in korean)
            candidates.append(
                (
                    prompt_score,
                    int(start == expected_start),
                    -abs(start - expected_start),
                    " ".join(raw_tokens[start:end]),
                    end,
                )
            )

    if not candidates:
        return "", expected_start
    candidates.sort(key=lambda item: item[:3], reverse=True)
    return candidates[0][3], candidates[0][4]


def run_sentence_spell(driver, card_count, da_e, da_k):
    progress = sentence_spell_progress(driver)
    completed = progress[0] if progress else 0

    while completed < card_count:
        before_count = completed
        solved_segments = []
        expected_start = 0

        while completed == before_count:
            if len(solved_segments) >= 30:
                raise RuntimeError("문장 스펠 한 카드의 구간 수가 30개를 넘었습니다.")
            prompt_element = visible_element(
                driver, ".CardItem.active .para_item.active"
            )
            prompt = (
                prompt_element.text.strip()
                if prompt_element is not None
                else ""
            )
            answer, answer_end = sentence_spell_answer(
                driver, prompt, da_e, da_k, expected_start
            )
            if not prompt or not answer:
                raise RuntimeError(
                    f"문장 스펠의 다음 구간을 찾지 못했습니다: 문제={prompt!r}"
                )

            previous_signature = sentence_spell_signature(driver)
            used_ids = set()
            for expected_token in answer.split():
                expected = normalize_sentence_token(expected_token)
                choice = None
                for element in driver.find_elements(
                    By.CSS_SELECTOR, ".CardItem.active .scramble-item"
                ):
                    try:
                        if (
                            element.id not in used_ids
                            and element.is_displayed()
                            and normalize_sentence_token(element.text) == expected
                        ):
                            choice = element
                            break
                    except Exception:
                        continue
                if choice is None:
                    raise RuntimeError(
                        f"문장 스펠 조각 {expected_token!r}을 찾지 못했습니다."
                    )
                used_ids.add(choice.id)
                driver.execute_script("arguments[0].click();", choice)
                time.sleep(0.08)

            expected_start = answer_end
            solved_segments.append(answer)
            print(f"문장 스펠 구간 처리: {answer}", flush=True)
            try:
                WebDriverWait(driver, 12).until(
                    lambda d: (
                        (
                            sentence_spell_progress(d) is not None
                            and sentence_spell_progress(d)[0] > before_count
                        )
                        or visible_element(d, ".btn-next-card") is not None
                        or (
                            sentence_spell_ready(d)
                            and sentence_spell_signature(d) != previous_signature
                        )
                    )
                )
            except TimeoutException as error:
                visible = driver.execute_script(
                    "return (document.body.innerText || '').slice(-1200);"
                )
                raise RuntimeError(
                    f"문장 스펠 다음 구간 전환을 기다리다 멈췄습니다: {visible}"
                ) from error

            progress = sentence_spell_progress(driver)
            if progress is not None and progress[0] > before_count:
                completed = progress[0]
                break

        print(
            f"문장 스펠 진행: {completed}/{card_count} - "
            f"{' / '.join(solved_segments)}"
        )

        next_button = visible_element(driver, ".btn-next-card")
        if next_button is None:
            raise RuntimeError("문장 스펠의 다음카드 버튼을 찾지 못했습니다.")
        driver.execute_script("arguments[0].click();", next_button)
        if completed < card_count:
            WebDriverWait(driver, 6).until(sentence_spell_ready)
        time.sleep(0.2)

    print(f"문장 스펠학습 처리 완료: {completed}/{card_count}")
    return completed


def restart_completed_section(driver):
    text = driver.execute_script("return document.body.innerText || '';")
    if "구간 학습이 완료" not in text and "새로 학습하기" not in text:
        return False
    reset = next(
        (
            element
            for element in driver.find_elements(By.CSS_SELECTOR, ".btn-reset-section")
            if element.is_displayed() and element.is_enabled()
        ),
        None,
    )
    if reset is None:
        return False
    driver.execute_script("arguments[0].click();", reset)
    try:
        confirm = WebDriverWait(driver, 4).until(
            lambda d: next(
                (
                    element
                    for element in d.find_elements(By.CSS_SELECTOR, ".btn-ok.btn-danger")
                    if element.is_displayed() and element.is_enabled()
                ),
                None,
            )
        )
        driver.execute_script("arguments[0].click();", confirm)
    except TimeoutException:
        pass
    time.sleep(0.5)
    return True


def start_spell(driver, timeout=35):
    end_time = time.time() + timeout
    while time.time() < end_time:
        if has_spell_input(driver):
            return "input"
        if sentence_spell_ready(driver):
            return "sentence"
        if restart_completed_section(driver):
            continue
        warning_ok = next(
            (
                element
                for element in driver.find_elements(
                    By.CSS_SELECTOR, ".modal.in .btn-ok, .modal.show .btn-ok"
                )
                if element.is_displayed() and element.is_enabled()
            ),
            None,
        )
        if warning_ok is not None:
            driver.execute_script("arguments[0].click();", warning_ok)
            driver.execute_script(
                """
                const select = document.querySelector('select.show_type');
                if (select) {
                  select.selectedIndex = Math.min(3, select.options.length - 1);
                  select.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """
            )
            time.sleep(0.4)
            continue
        direct_start = next(
            (
                element
                for element in driver.find_elements(By.CSS_SELECTOR, ".btn-opt-start")
                if element.is_displayed()
            ),
            None,
        )
        if direct_start is not None:
            driver.execute_script("arguments[0].click();", direct_start)
            time.sleep(1.2)
            continue
        try:
            click_first_available(driver, START_BUTTON_SELECTORS, timeout=1)
        except TimeoutException:
            pass
        time.sleep(0.5)
    if has_spell_input(driver):
        return "input"
    if sentence_spell_ready(driver):
        return "sentence"
    return ""


def click_confirm(driver):
    selectors = [
        "#wrapper-learn .btn-confirm",
        ".study-bottom .btn-confirm",
        "a.btn-confirm",
    ]
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if element.is_displayed() and element.is_enabled():
                try:
                    element.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", element)
                return True
    return False


TEXT_SELECTORS = [
    ".CardItem.showing .card-bottom .text-normal",
    ".CardItem.showing .card-top .text-normal",
    ".CardItem.showing .card-bottom",
    ".CardItem.showing .card-top",
    "#wrapper-learn .CardItem .text-normal",
    "#wrapper-learn .CardItem .card-bottom",
    "#wrapper-learn .CardItem .card-top",
    "#wrapper-learn .spell-question",
    "#wrapper-learn .question",
]

# 카드가 넘어가길 기다리는 동안 화면 상태를 한 번에 읽는다. 예전에는 카드
# 번호, 입력칸 존재 여부, 화면 글자를 따로 물어보느라 폴링 한 번에 브라우저
# 왕복이 서른 번 넘게 났다. 왕복 자체가 대기 간격만큼 걸려서, 카드마다
# 쓸데없이 몇 초씩 새어 나갔다.
CARD_STATE_JS = """
const cardSelectors = arguments[0], textSelectors = arguments[1];
const visible = el => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden'
        && rect.width > 0 && rect.height > 0;
};
let card = null;
for (const selector of cardSelectors) {
    for (const el of document.querySelectorAll(selector)) {
        if (visible(el)) { card = el; break; }
    }
    if (card) break;
}
const inputClass = window.cheat_input_class || '';
const inputSelectors = (inputClass ? ['input.' + inputClass] : [])
    .concat(["input[name='input_answer']", "input[type='text']", 'input']);
const findInput = root => {
    if (!root) return null;
    for (const selector of inputSelectors) {
        for (const el of root.querySelectorAll(selector)) {
            if (visible(el) && !el.disabled && !el.readOnly) return el;
        }
    }
    return null;
};
let input = findInput(card);
if (!input) {
    for (const other of
            document.querySelectorAll('#wrapper-learn .CardItem:not(.deactive)')) {
        input = findInput(other);
        if (input) break;
    }
}
const blocks = [];
if (card) blocks.push(card.innerText || card.textContent || '');
for (const selector of textSelectors) {
    for (const el of document.querySelectorAll(selector)) {
        if (visible(el)) blocks.push(el.innerText || el.textContent || '');
    }
}
return {
    cardId: card ? (card.getAttribute('data-idx') || '') : '',
    hasInput: !!input,
    input: input,
    blocks: blocks.filter(Boolean),
};
"""

EMPTY_STATE = {"cardId": "", "hasInput": False, "input": None, "blocks": []}


def read_card_state(driver):
    try:
        state = driver.execute_script(CARD_STATE_JS, CARD_SELECTORS, TEXT_SELECTORS)
    except Exception:
        return dict(EMPTY_STATE)
    return state or dict(EMPTY_STATE)


def find_prompt_card(blocks, da_e, da_k, examples):
    """화면에 떠 있는 카드가 몇 번인지 찾는다. 정답 자체는 고르지 않는다.

    스펠은 언제나 영어 철자를 입력하는 모드다. 예전에는 화면에서 읽어낸
    글자를 그대로 '문제'로 삼고 반대쪽 값을 답으로 넣었는데, 뜻이 화면에
    없고 예문만 보이는 카드에서는 예문 속 영어 단어가 문제로 잡혀 한국어를
    입력해 버렸다. 그래서 카드 번호만 찾고, 정답은 호출부가 da_e 에서 꺼낸다.
    """
    lines = []
    joined = ""
    for block in blocks:
        joined += "\n" + norm_text(block)
        lines.extend(norm_text(line) for line in block.splitlines())
    lines = {line for line in lines if line and line not in IGNORE_TEXTS}

    # 예문 -> 뜻 -> 영어 순으로 "한 줄 전체가 일치"하는 것을 먼저 본다.
    # 예문이 가장 확실하다. 뜻과 영어가 다른 카드의 예문 안에 섞여 있어도
    # 줄 단위 일치는 흔들리지 않는다.
    for terms in (examples, da_k, da_e):
        for index, value in enumerate(terms):
            term = norm_text(value)
            if term and term in lines:
                return index

    # 줄 단위로 못 찾으면 부분 문자열로 되돌아간다. 짧은 단어가 남의 예문에
    # 우연히 들어가는 일이 있어 가장 긴 것을 고른다.
    candidates = []
    for index, (english, korean) in enumerate(zip(da_e, da_k)):
        for term in (norm_text(korean), norm_text(english)):
            if term and term in joined:
                candidates.append((len(term), index))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return -1


def advance_to_next_card(driver):
    # 단어 스펠도 문장 스펠처럼 "다음카드" 버튼이 떠 있을 때가 있다. 버튼이
    # 없으면 페이지가 알아서 넘어간 것이므로 아무것도 하지 않는다.
    for selector in (
        "#wrapper-learn .btnNextCard",
        "#wrapper-learn .btn-next-card",
        "#wrapper-learn [class*='next-card']",
    ):
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if element.is_displayed() and element.is_enabled():
                    driver.execute_script("arguments[0].click();", element)
                    return True
            except Exception:
                continue

    # 틀린 카드는 "모르는 카드" 화면으로 넘어가 입력칸 없이 SPACE 를
    # 기다린다. 입력칸이 없을 때만 보내므로 답에 공백이 섞일 일은 없다.
    if not has_spell_input(driver):
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.SPACE)
            return True
        except Exception:
            pass
    return False


def wait_for_next_card(
    driver, da_e, da_k, examples, previous_index=-1, previous_card_id="", timeout=10
):
    end_time = time.time() + timeout
    while True:
        state = read_card_state(driver)
        card_id = state.get("cardId") or ""
        index = find_prompt_card(state.get("blocks") or [], da_e, da_k, examples)
        if index > 0 and state.get("hasInput"):
            # data-idx 가 있으면 그걸로 판단한다. 뜻이 같은 카드가 이어져도
            # 카드가 넘어간 걸 알아채고, 반대로 같은 카드에서 보이는 글자만
            # 잠깐 달라져도 속지 않는다.
            if card_id and previous_card_id:
                moved = card_id != previous_card_id
            else:
                moved = index != previous_index
            if previous_index < 0 and not previous_card_id:
                moved = True
            if moved:
                return index, card_id
        if time.time() >= end_time:
            return -1, previous_card_id
        # 카드 전환 중에는 입력칸과 문제가 동시에 잠시 사라진다.
        # 이때 확인/Enter를 다시 보내면 다음 카드를 건너뛸 수 있으므로 기다린다.
        # 상태 읽기가 왕복 한 번이라 짧게 돌아도 부담이 없다.
        time.sleep(0.08)


def submit_answer(driver, answer):
    last_error = None
    for _ in range(3):
        try:
            input_el = get_spell_input(driver)
            input_el.click()
            input_el.send_keys(Keys.CONTROL, "a")
            input_el.send_keys(answer)
            if not click_confirm(driver):
                input_el.send_keys(Keys.ENTER)
            # 채점이 끝나면 입력칸이 사라지거나 카드가 넘어간다. 예전에는
            # 무조건 0.75초를 잤는데, 대부분 그 전에 끝나 있었다.
            end_time = time.time() + 0.8
            while time.time() < end_time:
                if not read_card_state(driver).get("hasInput"):
                    break
                time.sleep(0.05)
            click_confirm(driver)
            return
        except StaleElementReferenceException as error:
            last_error = error
            time.sleep(0.2)
    raise last_error


class SpellingLearning:
    def __init__(self, driver: webdriver.Chrome):
        self.driver = driver

    def run(self, num_d: int, word_d: list) -> None:
        driver = self.driver
        wait = WebDriverWait(driver, 15)
        da_e, da_k, details = word_d
        examples = [
            detail.get("example", "") if isinstance(detail, dict) else ""
            for detail in details
        ]

        if not enter_spell(driver):
            raise TimeoutException("스펠학습 페이지에 진입하지 못했습니다.")
        learning_mode = start_spell(driver)
        if not learning_mode:
            state = driver.execute_script(
                "return (document.body.innerText || '').slice(-1200);"
            )
            raise TimeoutException(
                f"스펠학습 문제를 시작하지 못했습니다. 현재 화면: {state}"
            )
        if learning_mode == "sentence":
            card_count = max(1, num_d - 1)
            return run_sentence_spell(driver, card_count, da_e, da_k)
        time.sleep(0.3)
        last_index = -1
        last_card_id = ""
        completed = 0

        for _ in range(1, num_d):
            try:
                index, card_id = wait_for_next_card(
                    driver, da_e, da_k, examples, last_index, last_card_id
                )
                if index < 0:
                    # 카드가 스스로 넘어가지 않는 화면이 둘 있다. "다음카드"
                    # 버튼을 기다리는 화면과, 틀린 뒤 SPACE 를 기다리는
                    # "모르는 카드" 화면이다. 한 번 넘겨 보고 다시 기다린다.
                    if advance_to_next_card(driver):
                        index, card_id = wait_for_next_card(
                            driver, da_e, da_k, examples, last_index, last_card_id
                        )
                # 스펠은 언제나 영어 철자를 입력하는 모드다. 화면에서 읽은
                # 글자를 그대로 답으로 쓰지 않고 카드 앞면에서 꺼낸다.
                answer = norm_text(da_e[index]) if index > 0 else ""
                question = norm_text(da_k[index]) if index > 0 else ""
                print(f"문제: {question!r} / 입력: {answer!r}")
                if not answer:
                    state = driver.execute_script(
                        "return (document.body.innerText || '').slice(-1200);"
                    )
                    raise RuntimeError(
                        f"스펠 {completed + 1}번째 카드를 찾지 못했습니다. "
                        f"현재 화면: {state}"
                    )
                submit_answer(driver, answer)
                last_index = index
                last_card_id = card_id
                completed += 1
            except Exception as error:
                print(f"스펠 학습 중 오류가 발생했습니다: {error}")
                raise
        card_count = max(1, num_d - 1)
        print(f"스펠학습 처리 완료: {completed}/{card_count}")
        if completed < card_count:
            raise RuntimeError(f"스펠학습이 {completed}/{card_count}에서 중단되었습니다.")
        return completed
