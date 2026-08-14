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


def get_spell_input(driver):
    input_class = driver.execute_script("return window.cheat_input_class || ''; ")
    active_cards = driver.find_elements(
        By.CSS_SELECTOR,
        ".CardItem.current.showing, .CardItem.current",
    )
    active_selectors = []
    if input_class:
        active_selectors.append(f"input.{input_class}")
    active_selectors.extend([
        "input[name='input_answer']",
        "input[type='text']",
        "input",
    ])
    for card in active_cards:
        try:
            if not card.is_displayed() or card.rect["width"] <= 0:
                continue
            for selector in active_selectors:
                for element in card.find_elements(By.CSS_SELECTOR, selector):
                    if element.is_displayed() and element.is_enabled():
                        return element
        except StaleElementReferenceException:
            continue

    selectors = [
        "#wrapper-learn .CardItem.showing input[type='text']",
        "#wrapper-learn .CardItem.showing input",
        "#wrapper-learn input[type='text']",
        "#wrapper-learn input",
    ]
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if element.is_displayed() and element.is_enabled():
                return element
    raise TimeoutException("spell input not found")


def has_spell_input(driver):
    try:
        get_spell_input(driver)
        return True
    except Exception:
        return False


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


def get_visible_korean_prompt(driver, da_k):
    korean_terms = [norm_text(value) for value in da_k if norm_text(value) and value != 0]
    blocks = []

    # 첫 문제 이후에는 활성 카드에서 "showing" 클래스가 빠질 수 있다.
    # 현재 입력칸이 속한 카드를 기준으로 읽으면 카드 클래스 변경과 무관하게 동작한다.
    try:
        input_el = get_spell_input(driver)
        active_text = driver.execute_script(
            """
            const input = arguments[0];
            const card = input.closest('.CardItem') || input.closest('#wrapper-learn');
            if (!card) return '';
            const style = window.getComputedStyle(card);
            const rect = card.getBoundingClientRect();
            if (style.display === 'none' || style.visibility === 'hidden'
                    || rect.width === 0 || rect.height === 0) return '';
            return card.innerText || card.textContent || '';
            """,
            input_el,
        )
        if active_text:
            blocks.append(active_text)
    except (TimeoutException, StaleElementReferenceException):
        pass

    selectors = [
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
    for selector in selectors:
        blocks.extend(visible_texts(driver, selector))

    for block in blocks:
        block_text = norm_text(block)
        lines = [norm_text(line) for line in block.splitlines() if norm_text(line)]
        for line in lines:
            if line in IGNORE_TEXTS:
                continue
            if line in korean_terms:
                return line
        for term in sorted(korean_terms, key=len, reverse=True):
            if term and term in block_text:
                return term
    return ""


def get_english_answer(question, da_e, da_k):
    question = norm_text(question)
    for english, korean in zip(da_e, da_k):
        if norm_text(korean) == question:
            return norm_text(english)
        if norm_text(english) == question:
            return norm_text(korean)
    return ""


def wait_for_spell_prompt(driver, da_k, timeout=10, previous_question=""):
    end_time = time.time() + timeout
    while time.time() < end_time:
        question = get_visible_korean_prompt(driver, da_k)
        if question and question != previous_question and has_spell_input(driver):
            return question
        # 카드 전환 중에는 입력칸과 문제가 동시에 잠시 사라진다.
        # 이때 확인/Enter를 다시 보내면 다음 카드를 건너뛸 수 있으므로 기다린다.
        time.sleep(0.35)
    return ""


def wait_until_prompt_changes(driver, da_k, previous_question, timeout=6):
    end_time = time.time() + timeout
    while time.time() < end_time:
        question = get_visible_korean_prompt(driver, da_k)
        if question and question != previous_question and has_spell_input(driver):
            return question
        time.sleep(0.35)
    return ""


def submit_answer(driver, answer, da_k=None, question=""):
    last_error = None
    for _ in range(3):
        try:
            input_el = get_spell_input(driver)
            input_el.click()
            input_el.send_keys(Keys.CONTROL, "a")
            input_el.send_keys(answer)
            time.sleep(0.15)
            if not click_confirm(driver):
                input_el.send_keys(Keys.ENTER)
            time.sleep(0.75)
            click_confirm(driver)
            if da_k is not None:
                wait_until_prompt_changes(driver, da_k, question)
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
        da_e, da_k, _ = word_d
        prompt_terms = [*da_k, *da_e]

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
        time.sleep(1)
        last_question = ""
        completed = 0

        for _ in range(1, num_d):
            try:
                question = wait_for_spell_prompt(
                    driver, prompt_terms, previous_question=last_question
                )
                answer = get_english_answer(question, da_e, da_k)
                print(f"문제: {question!r} / 입력: {answer!r}")
                if not question or not answer:
                    print("스펠 문제 또는 정답을 찾지 못해서 중단합니다.")
                    break
                submit_answer(driver, answer, da_k=prompt_terms, question=question)
                last_question = question
                completed += 1
                time.sleep(0.5)
            except Exception as error:
                print(f"스펠 학습 중 오류가 발생했습니다: {error}")
                raise
        card_count = max(1, num_d - 1)
        print(f"스펠학습 처리 완료: {completed}/{card_count}")
        if completed < card_count:
            raise RuntimeError(f"스펠학습이 {completed}/{card_count}에서 중단되었습니다.")
        return completed
